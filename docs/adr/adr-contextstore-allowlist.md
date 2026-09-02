# ADR: ContextStore como ALLOWLIST — tipo declarado, mapa de dados e vocabulário único de política

**Status:** Aceito — 2026-08-26. **Parcialmente implementado:** V0 *(metade)* · V1 · V1b · V2 ·
V2b · **V3** · **D8** · **V5 (metade — a D6)** · **D7 (metade — o seed)** · **D9 FATIA 1** entregues.

**Estado vivo, medido em 2026-08-30:** catálogo **13 tipos**; mapa **94 canônicas / 82 aliases**,
`mode: "audit"`; auditoria acusando **1** campo não declarado (`session.preview`, deixado fora por
decisão). Gates verdes: `probe_context_map_audit.sh` · `probe_context_visibility_selector.sh` ·
`probe_seed_drift_named.sh` · `probe_legacy_display_rule_closed.sh` · `probe_type_catalog.sh`.

**A próxima fase é a V4, e ela NÃO é mais "a inversão"** — a D9 a redefiniu para *"ligar o portão
de PUBLISH sobre o cadastro"*, e ela é bloqueada por **três decisões abertas** (§ *Decisões que
esta emenda deixa ABERTAS*, itens 1–3), não por esforço de código. A outra metade da V5 — fechar
os aliases — depende do contador da V3 decair, logo é bloqueada por **TEMPO**.

⚠️ *Esta linha já ficou atrás da §6 uma vez (ver a nota de data abaixo) e ficou de novo entre
2026-08-30 e a FATIA 1: dizia "V4 (a inversão) é a próxima" depois de a §6 registrar a V4 como
REDEFINIDA. Ao entregar uma fase, o cabeçalho é a segunda casa a corrigir — e é a que mais gente lê.*
**Data:** 2026-08-26 *(cabeçalho corrigido em 2026-08-29: dizia "Proposto — nenhuma fase implementada"
enquanto a §6 marcava quatro fases como FEITAS, com gate e entrada de CHANGELOG cada uma. Documento que
se contradiz é da mesma família do DDL de `participation_intervals` — prosa afirmando um estado que
nada impõe —, e o dano aqui é concreto: quem lesse só o cabeçalho reimplementaria a V2.)*
**Componentes:** `packages/mcp-server-plughub`, `packages/schemas`, `packages/config-api`,
`packages/platform-ui`, `packages/skill-flow-engine`, `packages/sdk`,
`packages/orchestrator-bridge`, `packages/channel-gateway`, `packages/routing-engine`,
`packages/agent-registry`
**Relacionado:** [`adr-message-masking.md`](adr-message-masking.md) ·
[`docs/guias/context-masking-rules.md`](../guias/context-masking-rules.md) ·
[`docs/guias/context-store.md`](../guias/context-store.md) ·
[`docs/guias/context-store-taxonomy.md`](../guias/context-store-taxonomy.md) *(desatualizado — ver §1.6)* ·
[`adr-historico-unificado-duas-visoes.md`](adr-historico-unificado-duas-visoes.md) § F5 ·
`TODO.md` § *ARCO PROPOSTO — ContextStore como ALLOWLIST* (briefing que originou este ADR)

---

## 0. Sumário da decisão

1. **Tipo de dado** passa a ser a declaração única: um catálogo fechado onde cada tipo carrega
   **formato** (apresentação), **máscara por papel** e **classe LGPD**. Qualquer das três pode ser
   vazia — existe tipo que só formata, e tipo que não faz nada.
2. **Mapa do ContextStore**: uma árvore JSON declarativa em `escopo.dominio.campo`, onde cada folha
   declara o seu tipo. **O mapa é a allowlist.**
3. **Campo não declarado não é acessível** (inversão para deny-by-default), com um pré-requisito
   inegociável: **a omissão deixa de ser muda**.
4. **Alias do legado** (`caller.cpf` → `session.cliente.cpf`) declarado no próprio mapa, resolvido na
   borda, **contado e com data de remoção** — migração, nunca segunda grafia permanente.
5. São **quatro políticas** distintas (escrita, leitura por agente, exibição a humano, persistência)
   compartilhando **um** vocabulário. Fundi-las é erro; separá-las em quatro vocabulários é o estado
   atual.
6. **FINALIDADE é dimensão do tipo** (D8, 2026-08-30): campo cuja finalidade dispensa máscara ganha
   tipo próprio — nomeado pela finalidade, com a classe LGPD preservada —, nunca uma regra `plain`
   de exceção, que daria duas respostas à mesma pergunta. E o **discriminador mora no domínio**
   (`cartao.cpf`, não `cartao.cpf_titular`), porque o casador não tem glob de meio.
7. **O ContextStore vira CADASTRO** (D9, proposto 2026-08-30) — emenda que SUPERSEDE a D2 como
   allowlist-por-nome. O domínio inteiro é configurado antes do uso, como um `DialogForm`; o que
   não está registrado não passa no **publish** (o runtime nunca rejeita, resolve restritivo e
   loga). Tipagem e mascaramento resolvem-se no cadastro, e a visibilidade do pool vira SELEÇÃO
   sobre a estrutura cadastrada. Motivo medido: a enumeração de NOMES não fecha — 75 canônicas
   declaradas, **4** observadas, 13 correções vindas de 4 fluxos.

---

## 1. Contexto

### 1.1 O defeito de origem

`ContextMaskingConfig.default_unmatched_operator` é **`"plain"`** (`schemas/src/audit.ts:181`,
`config-api/seed.py:494`). Isto é **deny-nothing**: toda tag do ContextStore sem regra explícita é
exibida em claro ao operador — e, desde a F5 do arco de histórico, **persistida** em claro num
registro durável. A proteção depende inteiramente de alguém lembrar de escrever uma regra.

Varredura de 2026-08-26 (`infra/test/sweep_ctx_tags.sh`, 24 sessões vivas — **medição herdada,
não re-executada nesta sessão**):

| tag | sessões | o que era |
|---|---|---|
| `session.cpf` | 2 | CPF em claro. Havia regra para `caller.cpf`, não para `session.cpf` |
| `journey.numero_cartao` | 1 | cartão em claro. Havia para `session.numero_cartao`, não para `journey.` |
| `session.delegate_resume_token` | 11 | **capacidade** em claro, agora durável |

### 1.2 Por que é estrutural, e não esquecimento

`caller.*` e `account.*` têm catch-all. `session.*` e `journey.*` **não podem ter**: o próprio seed
avisa por escrito (`config-api/seed.py:507-509`) que um `session.* → hidden` derruba
`session.dialog_form_id` / `session.decisions` e a tela de aprovação **para de renderizar em
silêncio**. E é justamente em `session.` que o `delegate.context` de um workflow deposita os campos.

**Consequência: todo campo que um workflow passa adiante nasce desprotegido.**

O remendo aplicado em 2026-08-26 — globs de sufixo (`*.cpf`, `*.resume_token`, …) — protege por
**tipo de campo** em vez de por namespace, e cobre os tipos que *por acaso* conhecemos hoje. Um
`session.rg` amanhã nasce exposto de novo. É patch no modelo, não o modelo.

### 1.3 Achado — há ~~três (quase quatro)~~ **SETE** inventários de "categoria", e eles discordam

> ⚠️ **Corrigido em 2026-08-26 (fase V2), por varredura de símbolo.** Esta seção dizia *"três (quase
> quatro)"*; são **sete**, e os três que faltavam são justamente os que têm **produtor vivo em
> Python** — isto é, os que podiam divergir sem que nenhum teste de TypeScript notasse. A
> subcontagem tem a forma que o `CLAUDE.md` cataloga em *"contar sites por DERIVAÇÃO"*: o inventário
> foi feito por menção, e quem não usa o nome `DataCategory` não apareceu.

| # | fonte | valores | detecta? |
|---|---|---|---|
| 1 | `schemas/src/audit.ts` `DataCategorySchema` | cpf, credit_card, phone, email_addr, **address, health, financial** | — |
| 2 | `schemas/src/audit.ts` `DEFAULT_MASKING_RULES` | os 4 primeiros — **só 4** | **sim** |
| 3 | `platform-ui/.../MaskingPage.tsx` `DEFAULT_CATEGORIES` | os 4 + **iban, passport** | não |
| 4 | `platform-ui/.../MaskedToken.tsx` `MaskingRulesMap` → `rule.{category}` | chaveado pelos 6 da tela | não |
| 5 | `quality-ingest/.../masking.py` `DEFAULT_MASKING_RULES` | porta Python dos 4 | **sim** |
| 6 | `channel-gateway/.../adapters/webhook.py` `_PII_MASKERS` | 4 regexes **sem categoria nenhuma** | **sim** |
| 7 | `platform-ui/.../MaskedToken.tsx` `CATEGORY_META` | os 4 + **iban, passport**, rótulos hardcoded | não |

`iban` e `passport` **não existem** no enum e não têm regra nenhuma — e a tela renderizava o selo
**"Ativo"** neles **incondicionalmente** (sem ramo), além de oferecer editor de regra de canal que
gravava numa chave que ninguém lia. O comentário prometia *"mirrors DEFAULT_MASKING_RULES"* —
promessa sem produtor.

**E eles não só podiam divergir: divergiam.** Medido lado a lado com
`infra/test/q_masking_display_parity.sh` (5 vetores × 3 portas que aplicam masking): **nenhuma das
cinco linhas era unânime**. O mesmo CPF saía `*********00` (nº 2, o caminho vivo), `***.***.***.00`
(nº 5, cuja docstring declarava fidelidade ao TS) e `***.***.***-00` (nº 6). A única coincidência era
acidente aritmético. Nada no repositório comparava as portas entre si: cada uma tinha teste próprio,
todos verdes, todos medindo a porta contra ela mesma.

> ✅ **FECHADO na V2** (ver `CHANGELOG.md`). Os inventários 1–7 passaram a derivar de **um** catálogo
> (`masking.types` no config-api, espelhado por `DEFAULT_DATA_TYPE_CATALOG`); `DEFAULT_MASKING_RULES`
> deixou de ser lista literal e passou a ser **derivada** dele; os fantasmas saíram; e as três portas
> de masking foram alinhadas à semântica canônica, com gate de paridade
> (`infra/test/probe_masking_display_parity.sh`) que reprova a divergência **e** o caso vácuo em que
> as três não mascaram nada. As cópias Python **permanecem cópias** — o fim delas é lerem o catálogo
> em runtime, e isso exige recusar alto quando a config não vier. Fase própria.

### 1.4 Achado — os mecanismos de masking são três METADES, cada uma com uma dimensão que falta às outras

| mecanismo | chaveado por | dimensão exclusiva | dimensões que lhe faltam |
|---|---|---|---|
| `MaskingRule` + regex (`audit.ts:81,259`) | `DataCategory` | **detecção** em texto livre | papel, canal |
| `MaskingDisplayRule` (`MaskedToken.tsx:43`) | `rule.{category}` | **canal** — `display_screen` × `display_voice` (`beep`/`silence`/`speak_placeholder`) | papel |
| `ContextMaskingRule` → `ContextMaskingType` (`audit.ts:124,152`) | tag × role | **papel** | detecção, canal |

Nenhuma é errada; nenhuma é completa. São três recortes do mesmo objeto morando em três casas, e o
`iban`/`passport` fantasma é o que acontece quando o catálogo não tem dono.

> **O consumidor desta unificação já tem ADR próprio:**
> [`adr-masked-typed-declaration.md`](adr-masked-typed-declaration.md) — o `masked` do
> DialogForm/menu passa a nomear um tipo do catálogo. É ele que faz a linha do meio (canal) e a de
> baixo (papel) alcançarem um campo declarado; a V2b foi o pré-requisito dele.
>
> **Emenda de 2026-08-29 (V2b).** A linha do meio mudou de chave: `MaskingDisplayRule` já não é
> chaveada por `rule.{category}`, e sim pelo **id do tipo** no catálogo (`type.mascara.display`). A
> tabela acima descreve o estado do diagnóstico, não o de hoje. Isto era **pré-requisito do `masked`
> tipado**: enquanto a dimensão CANAL tivesse casa própria, "que máscara este campo usa?" teria duas
> respostas — e, como sempre, valeria a mais permissiva (o leitor legado vencia o catálogo). As
> outras duas metades seguem em casas separadas; fundi-las é a V3 em diante.

### 1.5 Achado — o ContextStore tem uma SEGUNDA porta, sem política ✅ FECHADO (V1b, 2026-08-26)

`applyContextMaskingDynamic` (`server.ts:1107`) tem **um único call site**: `server.ts:1679`, o
endpoint HTTP `GET /api/supervisor_state/:sessionId` que a Console consome.

O **tool MCP `supervisor_state`** (`tools/supervisor.ts:163-177`) monta o seu `context_snapshot`
fazendo `JSON.parse` do hash **cru** e o devolve em `:380` — **sem portão de namespace e sem
mascaramento**. É a mesma duplicação que mordeu a leitura de sentimento em 2026-08-25 (duas
implementações independentes, só uma consertada), agora sobre PII.

> ✅ **Fechado na V1b** (ver `CHANGELOG.md`). Provado ao vivo antes do conserto: `session.probe.cpf`
> injetado como `123.456.789-00` voltava **idêntico** pelo tool enquanto o endpoint entregava
> `***00` ao mesmo operador. A política mudou-se para **`lib/context-masking.ts`** — uma casa,
> importada pelas duas portas —, e o tool entrega em **grau operator, sem portão de namespace**
> (decisão do dono; o argumento é que nenhum dos dois consumidores daquela função tem visualizador
> com PAPEL, e no caso do tool o pool disponível é o de ENTRADA, não o que atende). Contrato novo:
> `customer_context.context_masking = { grade, total, hidden_count }` — a máscara **não é muda**.
> Gate `infra/test/probe_supervisor_tool_masking.sh`.
>
> ⚠️ **`R-agente` não foi tocado** (§D4): `@ctx.*` no fluxo continua cru por design. O que se fechou
> é a leitura em BLOCO do hash inteiro, que não é "o agente precisa deste campo".

### 1.6 Achado — o PREFIXO da tag roteia storage e TTL, e essa regra vive em pelo menos três casas

| escopo | hash Redis | TTL | decidido por |
|---|---|---|---|
| sessão | `{t}:ctx:{sessionId}` | 4 h | default |
| processo | `{t}:ctx:journey:{raiz}` | 30 d | prefixo `journey.` |
| cliente | `{t}:ctx:customer:{customerId}` | 90 d | prefixos `insight.historico`, `pricing` |

Implementações da mesma regra, medidas nesta sessão:

- `sdk/src/context-store.ts:50,58,106-120` — `LONG_TTL_PREFIXES` / `JOURNEY_TTL_PREFIX` / `ttlFor`
- `skill-flow-engine/src/interpolate.ts:237` — `tag.startsWith("journey.")` na leitura
- `mcp-server-plughub/src/tools/journey.ts:114,180` — `writeContextTag`, na escrita imperativa

**Isto é o que torna o prefixo load-bearing**: hoje a chave é auto-descritiva e nenhum componente
precisa de config para saber onde gravar. É também a razão de a taxonomia de `context-store-taxonomy.md`
estar simultaneamente certa (namespaces existem) e **errada onde importa**: ela declara `session.*` e
`journey.*` como *"não PII, visível completo"*, premissa que a varredura de 08-26 refutou.

### 1.7 Achado — não existe choke point de ESCRITA

> ⚠️ **O `12` desta seção era um número SEM CRITÉRIO, e por isso não dimensionava nada.**
> Remedido em 2026-09-02 (CNS-06): existiam **três** números para a mesma pergunta — 12 aqui,
> 16 numa contagem estrutural e 18 numa textual — e nenhum dizia o que contava. O critério
> agora está escrito no cabeçalho de `infra/test/_ctx_writer_census.py`, e o gate
> `infra/test/probe_ctx_writer_census.sh` impede que volte a ser opinião.

Medido em 2026-09-02, com critério declarado (escrita direta num hash de ContextStore, código
de produção, helper contado UMA vez): **8 arquivos · 22 sítios**.

| serviço | sítios |
|---|---|
| `orchestrator-bridge/.../main.py` | 10 |
| `channel-gateway/.../adapters/webhook.py` | 4 |
| `routing-engine/.../main.py` | 2 |
| `ai-gateway/.../sentiment_emitter.py` | 2 |
| `ai-gateway/.../copilot_emitter.py` · `evaluation-api/.../router.py` · `mcp-server/tools/bpm.ts` · `mcp-server/tools/journey.ts` | 1 cada |

**E o achado que reescreve a ALW-02: um funil JÁ EXISTE.** `writeContextTag`
(`mcp-server-plughub/src/tools/journey.ts`) centraliza a escrita e já é usado por
`server.ts` e `session.ts` — que por isso **não** aparecem na lista, apesar de o oráculo do
censo os listar. A tarefa deixa de ser *"construir um choke point"* e passa a ser
**"estender o que já está lá para o Python e rotear os 21 sítios restantes"**.

> ✅ **PASSO 1 EXECUTADO em 2026-09-02, e o número virou 7/21.** `tools/bpm.ts` saiu do
> `hset` cru e entrou no funil; com ele veio de carona um defeito latente (um `@mention`
> com tag `journey.*` caía no hash da SESSÃO e evaporava em 4 h). O funil também mudou de
> natureza: ele prometia no docstring *"só decide a CHAVE e o TTL, nunca o conteúdo"* e
> agora **carimba o `atributo`** (D9.6). E passou a receber o **objeto**, nunca o JSON já
> serializado — com `entryJson: string` qualquer chamador podia serializar por fora e
> passar ao largo do carimbo, mudo. Detalhe e gates no `CHANGELOG.md` de 2026-09-02.
>
> ⚠️ **21 contados, 20 a rotear.** `sentiment_emitter.py:163` escreve em
> `{tenant}:pool:{p}:sentiment_live`, não no ctx — é o erro para o lado de INCLUIR que o
> critério do censo assume, e o primeiro caso confirmado.

⚠️ **O oráculo e o instrumento divergem de propósito, e a divergência é a informação.** O
censo de cadastro lista 9 arquivos porque precisa varrer os NOMES das tags, e o nome está
literal no chamador; o censo de escritores lista 8 porque conta PONTOS a rotear. Cada
divergência é declarada no gate — um quarto item significa escritor novo ou cliente novo do
funil, e nos dois casos a ALW-02 mudou de tamanho.

### 1.8 Blast radius

`@ctx.` ou `:ctx:` aparecem em **486 ocorrências, 97 arquivos** (medido em 2026-08-26), dos quais ~40
são YAML de skill — isto é, **autoria**, não só código de plataforma. Este número é o que separa uma
mudança de chave de uma mudança de declaração.

> ⚠️ **O 486 NÃO se reproduz, e o denominador do mapa é outro** *(remedido em 2026-08-29, na V3)*.
> Contando `@ctx.<ns>.<campo>` em `packages/`: **231 ocorrências em 53 arquivos**. O 486 é
> compatível com um critério mais largo (`@ctx.` ∪ `:ctx:`, incluindo `docs/`), que mistura
> referência de tag com nome de CHAVE Redis e prosa. Não é o mesmo universo, e a diferença importa:
> o mapa se mede pelas TAGS.
>
> E o censo de leitura, sozinho, também não bastou — **escrita e leitura não coincidem**. As
> declarações `tag:` dos `context_tags` nos YAML trazem campos que nenhuma leitura menciona
> (`caller.telefone`, `caller.intencao_primaria`, `session.wrapup.*`, `account.status`). O mapa da
> V3 foi semeado sobre a UNIÃO das duas varreduras: **74 campos, 39 aliases**.

---

## 2. Decisão

### D1 — O TIPO é a declaração única

Substituir os três inventários de categoria por **um catálogo de tipos de dado**, onde cada tipo
carrega as três dimensões que hoje estão separadas:

| propriedade | o que governa | pode ser vazia? |
|---|---|---|
| `formato` | apresentação do valor quando visível (`R$ #.##0,00`, `###.###.###-##`) e, quando existir, o regex de **detecção** em texto livre | **sim** |
| `mascara` | mapa `papel → ContextMaskingType`; opcionalmente `canal → DisplayScreen/DisplayVoice` | **sim** |
| `lgpd` | classe do dado — eixo de autorização, retenção e auditoria (`pessoal`, `sensivel`, `financeiro`, `credencial`, `none`) | **sim** (`none`) |

Exemplos:

```
cpf         formato ###.###.###-##      mascara {operator: last_2}   lgpd pessoal
cartao      formato #### #### #### #### mascara {operator: last_4}   lgpd financeiro
moeda       formato R$ #.##0,00         mascara —                    lgpd —
texto       formato —                   mascara —                    lgpd —
token       formato —                   mascara {operator: hidden}   lgpd credencial
diagnostico formato —                   mascara {operator: full}     lgpd sensivel
```

**Por que tipo e não "categoria abstrata × formato" como dois eixos:** a proposta de dois eixos
*organiza* três mecanismos; o tipo os **funde**. E o custo de implementação é menor do que parece —
a propriedade `mascara` já está implementada (`ContextMaskingTypeSchema`, 9 valores, +
`applyMaskingTypeToValue` em `server.ts:1057`). O que é genuinamente novo é `formato`.

**Por que `lgpd` é propriedade do tipo, e não um segundo eixo:** tipo responde *"que forma tem"*;
não responde *"isto é dado pessoal sensível?"* — e `AuditPolicy.data_categories` precisa dessa
resposta, que é obrigação de LGPD. Dois campos do tipo `texto` podem ser um o motivo do contato e o
outro um diagnóstico médico. Resolver isto criando um tipo `diagnostico` mantém **um** ponto de
declaração; criar um eixo paralelo recria a divergência de §1.3 com nomes novos.

**Invariante:** um campo declara **exatamente um** tipo. Se dois campos precisam de políticas
diferentes, são dois tipos — não um tipo com exceção no campo.

### D2 — O mapa do ContextStore, em `escopo.dominio.campo`

Uma árvore JSON declarativa, no config-api, é a **fonte de verdade** de quais campos existem:

```
contexto:
  session:                          # ESCOPO — decide hash e TTL (§1.6)
    cliente:                        # DOMÍNIO de negócio — agrupa na tela
      cpf:            { tipo: cpf,   legado: "caller.cpf" }
      email:          { tipo: email, legado: "caller.email" }
    negocio:
      fatura:         { tipo: moeda, legado: "account.valor_fatura" }
  journey:
    processo:
      resume_token:   { tipo: token }
  customer:
    cliente:
      preferencia:    { tipo: texto }
```

**O escopo permanece o primeiro segmento da chave, e isto é deliberado.** A alternativa avaliada —
raiz por domínio de negócio (`cliente.cpf`) com o escopo declarado no nó — foi **recusada**: obrigaria
todo escritor a ter o mapa carregado para saber em qual hash gravar, e os escritores estão em
TypeScript **e** em Python. O `CLAUDE.md` § Configuration registra o caso medido dessa classe de
falha (o namespace `session` inerte no bridge por meses, por três causas empilhadas de leitura de
config, **todas degradando para "usa o default"**). Trocar roteamento auto-descritivo por roteamento
dependente de config, no caminho que decide **retenção de PII**, cria a mesma falha onde ela custa
mais: dado pessoal gravado num hash de 90 dias porque a config não carregou.

Com `escopo.dominio.campo` obtém-se a hierarquia de negócio **sem** um segundo roteador de storage:
o runtime lê o primeiro segmento (como hoje), a UI agrupa pelo segundo.

**O mapa é a allowlist.** Não há uma segunda lista.

### D3 — Alias do legado: declarado no mapa, resolvido na borda, CONTADO e DATADO

A canônica é a nova (`session.cliente.cpf`); o nome atual (`caller.cpf`) vira `legado` **no próprio
nó do mapa** — não numa segunda config, porque duas casas é como as cópias divergem.

> **Emenda medida na V3 (2026-08-29): `legado` é ARRAY, não string.** O exemplo acima está no
> singular, e a varredura achou **duas grafias vivas para o mesmo campo** — `caller.cpf`
> (`agente_contexto_ia_v1.yaml:111,230`) e `session.cpf` (depositado pelo `delegate.context`, o
> achado que motivou os globs de sufixo em 08-26). Com `legado` escalar sobrariam duas saídas, ambas
> ruins: **dois nós canônicos** para um campo — o que quebra a própria condição 2 abaixo, já que
> passariam a existir duas canônicas — ou **descartar um alias em silêncio**, que é o vazamento que
> o arco existe para matar. Hoje 39 aliases apontam para 74 canônicas.

Três condições, e sem elas o alias é dívida em vez de migração:

1. **Resolve na BORDA, antes de qualquer decisão de política.** Nenhuma regra, nenhum nó de mapa,
   nenhuma allowlist é escrita contra alias. Uma regra escrita contra `caller.cpf` enquanto a escrita
   chega como `session.cliente.cpf` seria uma **regra que não regra** — exatamente o defeito que este
   arco existe para matar.
2. **Só a canônica é ARMAZENADA.** O alias vive na leitura e na escrita, nunca no hash. Duas grafias
   no mesmo hash quebram o merge por confiança (`highest_confidence` compara campo com campo).
3. **Cada resolução via alias é CONTADA e logada.** Sem contador não há como saber quando remover, e
   é só isso que separa migração de grafia permanente. A remoção do alias é **medida** (contador
   zerado por N dias), não agendada por opinião.

**Por que a canônica é a nova, e não o legado:** se o legado for canônico, o caminho de menor
resistência mantém todo mundo escrevendo o nome antigo e o modelo novo nunca sai do papel — vira
rótulo de tela.

### D4 — Quatro políticas, um vocabulário

O vocabulário (tipos + mapa) é único. Os **pontos de imposição** são quatro e permanecem distintos:

| política | pergunta | hoje | onde se impõe |
|---|---|---|---|
| **W — escrita** | que campos podem existir no ContextStore | **não existe** | `ContextAccumulator` / `context_set` / SDK — e os 12 `HSET` de §1.7 |
| **R-agente** | o que o fluxo/IA lê via `@ctx.*` | **sem política** — devolve cru, **por design** (o agente precisa do CPF real para chamar o CRM) | `interpolate.ts` |
| **R-humano** | o que o operador vê na Console | existe; é a única que existe — e tem duas portas, uma sem gate (§1.5) | `applyContextMaskingDynamic` + `tools/supervisor.ts` |
| **P — persistência** | o que fica no registro durável (F5) | existe: máscara de valor em grau operator, **sem** portão de namespace | `maskContextForPersistence` (`server.ts:1247`) |

**Fundi-las é erro, e já há decisão escrita sobre isso:** aplicar o portão de namespace na
persistência faria a config de UI de um pool **apagar história em silêncio** (`server.ts:1206-1217`).
A separação fica, o vocabulário é que passa a ser um.

**R-agente permanece sem máscara**, e isto é decisão, não omissão: mascarar o valor que o fluxo lê
quebraria toda integração de domínio. O controle sobre o agente é outro — permissão de tool e
auditoria MCP — e vive noutro ADR.

### D5 — Deny-by-default, **depois** de a omissão deixar de ser muda

A inversão (`default_unmatched_operator: "hidden"`, campo fora do mapa não é acessível) é a decisão
central. Ela inverte o modo de falha: esquecer uma declaração passa a ser *"o operador não vê um
campo"* — visível, reclamável, corrigível — em vez de vazamento silencioso.

**Pré-requisito inegociável:** hoje `applyContextMaskingDynamic` faz `continue` em dois pontos
(`server.ts:1163` no portão de namespace, `:1179` no `hidden`) e o campo **some sem dizer**. Virar a
chave antes de consertar isso troca um vazamento de PII por uma **quebra muda de UI** — trade pior,
porque vazamento se descobre auditando e tela que some sem motivo se descobre com o operador parado.

O conserto já existe em forma, na F5 (`server.ts:1268-1271`): **contar, não omitir** — a entrada
permanece com `value: null`, `category: "hidden"`, e um `hidden_count` em campo próprio. A leitura
recebe o mesmo tratamento e a UI mostra *"3 campos ocultos por política"*, nomeando quais.

**Esta fatia vale sozinha, mesmo que a inversão nunca aconteça.**

### D6 — A tela do pool vira seletor sobre o mapa

`pools.context_visibility` (`agent-registry/prisma/schema.prisma:69`) é hoje editado como **texto
livre separado por vírgula** (`PoolsPage.tsx:1512,1520`). Passa a ser seletor sobre os nós do mapa.

Foi texto livre que permitiu a dica prometer o namespace `journey` num default que nunca o teve
(`DEFAULT_OPERATOR_NAMESPACES = ["service","session"]`, `server.ts:901`), corrigido em 2026-08-26
sem mecanismo que impeça a volta. O seletor **é** o mecanismo: não há como digitar um namespace que
não existe.

> **Confirmado ao implementar (2026-08-29), e pior do que este parágrafo supunha.** A correção de
> 08-26 arrumou a dica i18n e **deixou o placeholder do próprio campo dizendo `service, journey,
> session`** — a promessa sobreviveu a três linhas de distância do conserto. Contadas, eram **quatro**
> casas afirmando o default, todas discordando entre si (código, dica, placeholder, docstring do
> schema). Mais: **`service` e `history` não têm produtor nenhum** — zero ocorrências em `packages/`
> e zero no ContextStore vivo —, e `service` está no default. O seletor derruba a classe inteira
> porque a lista deixa de ser escrita: ela é derivada do mapa (`contextVisibilityOptions`).
>
> **Duas propriedades que a D6 não previa e são load-bearing:** (a) valor legado fora do mapa é
> MANTIDO e marcado — um seletor que só expressasse as opções descartaria política em silêncio no
> primeiro save; (b) **a limpeza passou a existir** — não havia caminho para esvaziar
> `context_visibility`, em três camadas mudas, e o seletor tornou o gesto natural.

### D7 — Fonte de verdade: config-api. O arquivo apenas semeia — ✅ **entregue 2026-08-29** *(metade do seed)*

Medido em 08-26: o `seed.py` e a config viva **divergiram nos dois sentidos**
(`session.vencimento_cartao` só no seed; `session.cpf_titular` só vivo), porque o seed foi editado
depois de a base estar semeada e seed-if-absent nunca reaplicou.

Decisão, alinhada à invariante de provisionamento do `CLAUDE.md`: **o config-api é a fonte de
verdade; o arquivo declarativo apenas semeia base vazia.** O que muda é que a divergência deixa de
ser invisível — o seed passa a **comparar e logar** o que difere em vez de pular mudo, e a tela mostra
a proveniência de cada nó (global × override de tenant).

**Entregue:** a metade do seed. `config_drift.describe_divergence` (stdlib puro, testável sem DB) e
o `divergent` do `seed()`. A metade da TELA (proveniência global × override por nó) **segue aberta**.

**Denominador, medido antes de mudar:** **77 keys semeadas, 0 ausentes, 76 iguais, 1 divergente.**
Um número pequeno, e dizê-lo é a entrega: a D7 não descobre uma base podre, ela remove a
possibilidade de uma base podre passar despercebida. Nas horas anteriores à medição haviam nascido
**duas** divergências (`masking.types` e `masking.context_rules`) e nenhuma tinha se anunciado.

**A divergência tem DUAS direções, e é por isso que o seed não conserta.** Reaplicar uma key
acrescenta o que só está na declaração **e descarta o que só está no banco**. No `__global__` vivo,
`masking.context_rules` tem 10 regras só no declarado *e* `session.cpf_titular` só no gravado — que
nenhum glob declarado cobre (`*.cpf` casa o **sufixo** `.cpf`, não `cpf_titular`). Um `--overwrite`
cego derrubaria aquele campo para `default_unmatched_operator: "plain"`. Logo o relatório separa os
lados e publica `overwrite_would_drop`: é o único número que responde *"posso reaplicar esta key sem
perder nada?"*. **Escolher um lado é decisão de política, não de mecanismo.**

**Achado que reposiciona a própria D7 — a DECLARAÇÃO pode estar velha.** Medido em 2026-08-29: o
`seed.py` **dentro do container** estava atrás do repositório (imagem sem bind mount), e a primeira
medição de divergência julgou a declaração errada — deu 2 em vez de 1. Um `divergent=0` vindo de uma
imagem atrasada é uma afirmação sobre um arquivo que não é o do repositório. É a família
*"existe ≠ é o de agora"*, agora dentro do instrumento que existe para acabar com o silêncio. Virou
o **ramo A** do gate, que compara os hashes.

**Gate:** `infra/test/probe_seed_drift_named.sh` (6 ramos: declaração atual · nomeia · duas direções
· testemunha negativa · não cura · comparador puro). Bateria de mutação com 4 mutações, todas pegas
— e a primeira tentativa da mutação da testemunha negativa **não pegou**, porque foi neutralizada
pela segunda guarda do comparador; a realista era comparar contra a LINHA em vez do `value` dela,
que é exatamente o erro que o mock do `test_store.py` carregava.

---

### D8 — FINALIDADE é dimensão do TIPO; o DISCRIMINADOR mora no domínio — ✅ **decidido 2026-08-30**

Duas decisões do dono, tomadas ao fechar as três folhas que faltavam ao mapa da V3. Ambas
estendem a D1 e a D2 em vez de as emendarem, e ambas nasceram de medição, não de simetria.

#### D8.1 — Campo cuja FINALIDADE dispensa máscara ganha TIPO próprio, nunca exceção de regra

A D1 diz que o tipo é `formato × máscara-por-papel × classe LGPD`. Faltava um eixo, e ele
apareceu num campo concreto: `session.portabilidade.numero_atual` é a **linha sendo portada** —
um telefone, mas **objeto do atendimento**, não dado de cadastro. Sem esse eixo, ele e
`session.cliente.telefone` eram o **mesmo `phone`**, e a única saída teria sido uma regra
`plain` explícita para a tag.

**Essa saída é a errada, e o motivo é o do próprio arco:** com a regra dizendo `plain` e o mapa
dizendo `phone`, *"que máscara este campo usa?"* teria **duas respostas**, e a mais permissiva
venceria — a mesma forma que a V2b removeu do leitor legado de canal. A finalidade entra
portanto como **TIPO** (`linha_em_servico`), com três propriedades load-bearing:

| propriedade | por quê |
|---|---|
| nome pela **FINALIDADE**, nunca pelo formato | um `phone_open` é arma carregada apontada para o próximo telefone de cadastro |
| `lgpd` **preservado** (`pessoal`) | o que se declara vazio é a MÁSCARA, nunca a CLASSE — um relatório LGPD tem de seguir dizendo que um telefone foi coletado. `texto` (`lgpd: "none"`) seria a economia que mente |
| `declared_only: true` **obrigatório** | a detecção olha o VALOR, e o valor não diz a finalidade; dois tipos com o mesmo regex seriam ambíguos em texto livre. É a D5 do ADR do `masked` tipado, aplicada ao mapa |

`by_role` vazio ⇒ **inelegível a `masked:`** por `typeMasksSomething`, como o `texto`. Correto:
tipo que não esconde nada não pode declarar que algo está escondido.

⚠️ **A §1.1 não muda de sentido.** O defeito nunca foi *"o valor está visível"*; foi *"o valor
está visível **porque ninguém decidiu**"*. `numero_atual` sai do `default_unmatched_operator` e
passa a ser uma **declaração**, que é exatamente o que a inversão da V4 existe para exigir.

⚠️ **Evidência que se mostrou fraca, registrada para não ser reusada.** Ao propor mascarar o
campo, argumentei que a plataforma *"já o protege uma borda ao lado"* — `_LEGACY_PREVIEW_SPEC`
(`webhook.py:2298`) o mascara `last_4`. **Não sustenta**: aquele preview vai ao **cliente**, na
retomada cross-canal, e ali mascarar é **anti-enumeração** (não confirmar dado a quem ainda não
provou posse). Audiência e finalidade diferentes — as duas bordas podem divergir com razão, e é
justamente disso que a D8.1 trata.

#### D8.2 — O discriminador mora no segmento de DOMÍNIO, nunca no nome da folha

O mapa da V3 trazia `session.cartao.cpf_titular`. Decisão: a folha canônica é **`cpf`**, e o
discriminador (*de quem* é o CPF) fica no domínio — `cartao`. Não é preferência estética; é o
único desenho que o mecanismo suporta:

> **Medido (`lib/context-masking.ts:80-160`):** o casador aceita **exato**, **sufixo** (`*.x`,
> por fronteira de segmento), **prefixo** (`x.*`) e `*`. **Não há glob de meio.** Um `*cpf*`
> cairia no ramo *"non-glob pattern that isn't an exact match"* e seria **regra inerte**, sem
> nada ficar vermelho.

Logo *"CPF protegido independentemente de qual CPF"* só é alcançável com a tag terminando em
`.cpf` — e é o glob genérico `*.cpf`, que já existe, que passa a cobrir a família inteira. O
princípio pedido pelo dono (*"declarar só o genérico; o cadastro aponta para o canônico"*) e o
mecanismo coincidem, e o `legado[]` da D3 **é** esse apontamento.

**Critério derivado, para toda folha nova:** se o nome da folha carrega um qualificador
(`_titular`, `_origem`, `_alternativo`), o qualificador pertence ao domínio, não ao campo.

#### D8.3 — A lacuna do CATÁLOGO fecha no catálogo, e só então o mapa cresce

`session.vencimento_cartao` ficou **fora** da V3 de propósito: tinha política viva (`last_2`) e
nenhum dos 11 tipos casava máscara **e** classe (`credit_card` é `last_4`, e sobre `MM/AA` isso
mostraria quase tudo — o argumento da T6 para o CVV). Fechou-se **no catálogo**, com
`card_expiry`, e só depois o campo entrou no mapa como `session.cartao.vencimento`.

**A ordem é o critério, e vale como regra:** catálogo primeiro, mapa depois. O inverso —
declarar um tipo aproximado para o campo caber — escreveria no mapa uma política que ninguém
decidiu, e a V4 a aplicaria.

⚠️ **`card_expiry` é tipo de LEITURA, nunca de COLETA.** Ele mascara algo, logo é elegível a
`masked:` pelo portão da T5 — e declará-lo no campo do formulário quebraria o pacote de
aprovação **em silêncio**: pela D4 do ADR do `masked` tipado, masked nunca entra em
`pipeline_state`, e é de lá que `skill_limite_entrada_v1.yaml:475` lê o valor para escrever a
tag. O formulário declara `masked` só no `cvv`, e isso é desenho — ver
`infra/dialog/dialog_limite_solicitacao.json`. O portão não distingue os dois usos; quem
distingue é esta linha.

#### Estado após a D8

Catálogo **13** tipos (`verifyDataTypeCatalog`: órfãos `[]`, categorias sem tipo `[]`); mapa
**75 canônicas / 40 aliases**, as quatro listas do `verifyContextMap` vazias.
*(Superado no mesmo dia pela FATIA 1 da D9: **94 / 82**. Esta linha fica como o estado em que a D8
fechou — o número dela é o antes, não o corrente.)*

✅ **Store vivo reaplicado em 2026-08-30** (`--overwrite` das três keys + restart do config-api;
o cache é em processo, re-semear não basta). A API serve 13 tipos e 75/40, e o gate
`probe_context_map_audit.sh` fecha com **16 asserções verdes**.

O **ramo F do gate INVERTEU**, e a inversão é a entrega: ele usava `session.vencimento_cartao` como
testemunha de *"a lacuna deliberada é acusada (`unknown`)"*, e a D8.3 fechou essa lacuna. A asserção
não foi removida — a proposição passou a ser *"a lacuna está FECHADA e não volta em silêncio"*.
Provado por mutação (removido o alias, o ramo fica vermelho).

⚠️ **O `tenant_demo` mantém override próprio de `context_rules`** — o seed só escreve `__global__` —
e ele difere por uma regra em cada direção (tem `session.cpf_titular`, não tem a exata
`session.vencimento_cartao`). Sem lacuna de comportamento: o glob `*.vencimento_cartao` está nos
dois. Limpá-lo é ato sobre config de tenant.

### D9 — O ContextStore vira CADASTRO: o que não está registrado não pode ser usado — 🆕 **proposto 2026-08-30**

> **Emenda que SUPERSEDE parte da D2 e redefine a D5.** Proposta do dono depois de a V3
> ser exercitada com tráfego real. O que cai não é o rigor — é a **natureza do problema**.

#### O que a medição derrubou

A D2 desenhou o mapa como uma **enumeração de NOMES**. Nomes são autorados pelo tenant: a
população é **aberta**. Medido em 2026-08-30, com tráfego real:

| medição | valor |
|---|---|
| canônicas declaradas no mapa | **75** |
| canônicas observadas sendo ESCRITAS | **4** |
| aliases que faltavam, achados numa passada | **13** |
| campos ainda sem canônica nenhuma | **7** |
| fluxos que produziram isso | **4**, de 44 skills, num tenant |

Quatro fluxos e o mapa já precisou de 13 correções. Não há horizonte de convergência: cada
fluxo novo que alguém autora acrescenta nomes que ninguém declarou. **A V4 estava bloqueada
por uma enumeração que não fecha.**

E há um segundo defeito, apontado pelo dono e mais grave que o primeiro: **os tipos criados
neste arco saíram de cenários de DEMO** (`skill_limite_processo_v1`,
`agente_portabilidade_intake_v1`). `card_expiry` ainda se defende — vencimento de cartão é
uma classe real. **`linha_em_servico` é o caso claro**: a *distinção* que ele carrega
(finalidade × cadastro) é geral e boa, mas o *tipo* nasceu da semântica de um fluxo de
demonstração. Extrair regra geral de exemplo proprietário é o erro, e ele estava em curso.

**A separação que faltava:**

| | catálogo de **tipos** | mapa de **nomes** |
|---|---|---|
| população | **fechada** (13 hoje) | **aberta** — o tenant autora |
| generaliza? | sim — CPF, telefone, cartão são classes de qualquer tenant | não — `session.approval_threshold` é o cenário de um cliente |
| mantenível por | a plataforma | ninguém, por construção |

A V3 fundiu os dois e apoiou a allowlist na metade que não fecha.

#### A decisão

**O domínio inteiro do ContextStore passa a ser CONFIGURADO antes do uso**, como um
`DialogForm`: declarado, versionado, publicado, e só então consumido. **O que não está
cadastrado não pode ser usado.**

O cadastro é a fonte única de: o campo existir · o **domínio** em que mora · o **tipo**
(`atributo`) e, por consequência, **máscara por papel e classe LGPD**.

Isto responde a §2 do briefing do dono — *"o ContextStore nasceu para passar dados úteis
adiante e nunca foi padronizado"*. O mapa era uma tentativa de impor ordem **retroativamente,
por enumeração**; só funciona com população fechada, e ela não é.

#### D9.1 — O portão é no PUBLISH; o runtime nunca rejeita

*"Não pode ser usado"* está certo como **contrato** e seria perigoso como **runtime**: escrita
rejeitada em execução faz um erro de cadastro derrubar atendimento em curso.

A casa já decidiu isto uma vez — D3 do `adr-masked-typed-declaration.md`. **Duas guardas,
posturas opostas:**

| momento | postura |
|---|---|
| **publish do skill** | **RECUSA**, nomeando a tag não cadastrada. Alto, estático, antes de rodar |
| **runtime** | **nunca rejeita a escrita**; grava, resolve para o mais restritivo e **LOGA nomeando** |

Sem a segunda metade, a primeira troca um vazamento por uma queda — e a §"Postura de
Engenharia" já cataloga esse padrão.

#### D9.2 — A pré-condição é ser estaticamente enumerável, e foi MEDIDA

O modelo só é viável se a população puder ser descoberta sem tráfego. Medido em 2026-08-30
sobre `packages/skill-flow-engine/skills/`:

- **21** escritas declaradas em YAML — **todas com nome literal**;
- **zero** `tag:` com interpolação;
- **zero** chaves de `delegate.context` compostas em runtime.

**Consequência que é o ganho central:** o problema deixa de ser de **observação** (rodar
tráfego, esperar aparecer, repetir até secar) e passa a ser de **análise estática** (varrer os
artefatos no publish). O *loop-until-dry* que a fase anterior exigia **deixa de ser
necessário**, e a lista de migração é produzível hoje.

> ✅ **CONFIRMADA, e a lista foi produzida** — censo de 2026-08-30 em
> [`docs/product/contextstore-cadastro-censo.md`](../product/contextstore-cadastro-censo.md):
> **91 nomes escritos, 0 dinâmicos**, 37 a cadastrar. Os dois únicos casos de composição são
> `segment.{segId}.<folha literal>` — a família da D9.4.
>
> ⚠️ **Emenda de MÉTODO, e ela é load-bearing: o extrator NÃO é um walker de YAML.** A D9
> contava duas superfícies de autoria; são **seis**, e quatro não aparecem caminhando a
> árvore — `context_json` é uma **string JSON** dentro do YAML (7 skills, 15 nomes),
> `context_set`/`context_write` guardam o nome em `input.tag` (um campo de step como outro),
> `delegate.context`/`collect.context` têm o prefixo `session.` **composto no gateway** (o
> nome final não está no arquivo), e o resto é literal em código de plataforma. Um portão
> escrito da forma óbvia ficaria verde com quatro superfícies passando por baixo: **fail-open
> por invisibilidade**, que é o *valor plausível* do lado do instrumento.

#### D9.3 — O cadastro tem DUAS origens, não uma

Boa parte das tags é escrita por **código de plataforma** — channel-gateway (`ctx_writes`),
orchestrator-bridge, routing-engine, mcp-server, ai-gateway. Nenhum portão de publish de skill
alcança esses.

| origem | registra | onde |
|---|---|---|
| **plataforma** | tags escritas pelo próprio código | seed (é o que o `DEFAULT_CONTEXT_MAP` já é) |
| **tenant** | tags autoradas em skills/flows | configuração, pela tela |

Mesma partição de `infra/modules.yaml` × config por tenant. Sem ela, o portão pareceria
fechado e metade dos escritores passaria por fora.

> ⚠️ **EMENDA (censo de 2026-08-30): são TRÊS origens, e a terceira não tem portão.** O corpo
> HTTP do webhook (`POST /v1/channels/webhook/pool/{id}`) traz um objeto `context` e o
> gateway escreve **cada chave verbatim, sem prefixo** (`webhook.py:630`). É por aí que
> existem as duas únicas tags **sem namespace nenhum** — `campaign_id` e `target_pool`,
> lidas por quatro skills —, e a rota é anônima por construção. Nenhuma análise estática de
> artefato a alcança: o nome só existe em runtime, na requisição. Para esta origem sobra
> exclusivamente a postura de runtime da **D9.1** — grava, resolve restritivo, LOGA. O
> cadastro pode declarar o que ela tem PERMISSÃO de escrever; não pode impedir na origem.

#### D9.4 — Prefixos dinâmicos continuam FAMÍLIA, nunca folha

`agent.{participantId}.*` e `segment.{segId}.*` não são enumeráveis campo a campo — o segundo
segmento é id de runtime. O mapa já os trata como terceiro balde, e o cadastro precisa manter
a noção de **família registrada**, senão a lista nasce impossível de fechar e o número que
autoriza a inversão vem inflado por campos que não podem ser declarados.

#### D9.5 — ~~Conteúdo LIVRE precisa de marcação própria~~ — ⛔ **DEPRECIADA pela própria D9** *(2026-08-30, no mesmo dia)*

> **Quem a depreca é a D9**, não a redefinição da V4 — a redefinição é consequência daquela.
> E a forma é incomum o bastante para valer registro: **é uma sub-decisão superada pelo
> PRÓPRIO PAI**. A D9.5 foi escrita junto com a D9, a partir da intuição pré-cadastro, e
> afirmava *"o cadastro não basta"*. Medido, o cadastro basta.

**As três medições que a derrubam:**

1. **Todo campo TEM onde ser cadastrado.** `ContextMapFieldSchema.tipo` é `z.string()`
   conferido contra o catálogo — o mapa aceita qualquer um dos 13 tipos, `opaque` inclusive.
   A afirmação *"8 campos não têm onde ser cadastrados"* (escrita no censo de 2026-08-30 e
   propagada para quatro arquivos) é **falsa**, e foi ela que promoveu esta decisão a
   pré-requisito.
2. **O mecanismo que ela invocava NÃO EXISTE.** Ela dizia que a metade de detecção do
   catálogo *"deixa de ser complemento e vira a única defesa"*, como se bastasse ligá-la.
   `formato.detect_pattern` tem **zero consumidores** — só declarações, no catálogo TS e
   espelhadas no `seed.py`. O motor que de fato existe (`MaskingService.applyMasking`) lê
   `MaskingRule.pattern`, roda em **um único sítio** (`tools/session.ts:472`) e **só sobre
   mensagens do stream** — nunca sobre valor de ContextStore.
3. **A D9 já resolve o que ela existia para resolver.** O defeito da §1.1 é *valor visível
   **porque ninguém decidiu***. Sob o cadastro, escolher `texto` para um campo de prosa é
   decisão explícita e auditável. Pode ser decisão RUIM — mas garantir que a decisão seja boa
   nunca foi o contrato deste arco.

**O que fica verdadeiro, e é mais estreito:** a declaração de um campo de prosa é uma APOSTA,
não uma descrição. `tipo: cpf` é verdade sobre todo valor futuro daquele campo; `tipo: X` em
`sugestao_resposta` é um palpite sobre conteúdo que ainda não existe. Isso não abre buraco no
modelo — muda o que a TELA de cadastro deve **dizer** a quem escolhe. Afordância, não
mecanismo.

**Os dois sobreviventes NÃO são "D9.5 depois"** — são itens diferentes que apenas coabitavam
aquela nota, e foram para casas próprias:

| sobrevivente | onde foi parar |
|---|---|
| granularidade em prosa (*"mostre o útil, esconda o CPF que ele cita"*) | **feature nova**, não pendência: exige levar o motor de detecção ao caminho de leitura do ctx — segundo sítio de detecção para manter. Gatilho: o primeiro contato real em que um campo de prosa carregue valor não-vazio. Hoje **6 dos 8 têm zero dado**, e os 2 que têm são **template autorado com um buraco tipado** (`R$ {{@ctx.session.limite_solicitado}}`), não prosa |
| classe LGPD para **não-cliente** (`session.reviewer_id`, usuário da plataforma) | decisão aberta #5 abaixo — é lacuna de CLASSE, não de detecção, e nada tem a ver com conteúdo livre |

⚠️ **Risco que a versão original não via, e que sozinho já desaconselhava o caminho:**
`session.summary` é lido em `DialogFormRenderer.tsx:232` a partir de
`supervisorState.customer_context.context_snapshot` — **através da porta de masking**.
Mascarar por default apaga a tela de aprovação, que é o *"troca vazamento de PII por quebra
muda de UI"* do pré-requisito da V4.

**Consequência prática: a migração da D9 deixa de ter bloqueio.** São 37 campos a cadastrar,
o trabalho é escolher um tipo para cada, 27 são óbvios, e nenhum espera decisão de mecanismo.

<!-- TEXTO ORIGINAL, mantido como registro do que se pensava:


`caller.note` e `caller.observacao` são o caso em que **o cadastro não basta**: o campo está
registrado, mas o *valor* pode trazer qualquer coisa. Anotação de agente é certeza em
produção, não hipótese.

É onde a metade de **DETECÇÃO** do catálogo (`formato.detect_pattern`) deixa de ser
complemento e vira a única defesa. O cadastro é o lugar certo para declarar *"este campo é
conteúdo livre"* — e essa marcação é o que liga a detecção sobre ele.

> ⚠️ **PROMOVIDA A PRÉ-REQUISITO pelo censo de 2026-08-30.** Ela era nota de rodapé sobre
> `caller.note`; medida, é **o que bloqueia a migração**. Dos 37 campos a cadastrar, 27 são
> identificador/enum e viram `texto` sem política — mas **8 não têm onde ser cadastrados**:
> `approval.summary` · `session.summary` · `session.parecer` · `session.resultado` ·
> `session.pergunta_coleta` · `session.copilot.sugestao_resposta` ·
> `session.copilot.acoes_recomendadas` · `session.copilot.flags_risco`. Todos carregam prosa
> de LLM ou de humano **sobre a conversa**, logo podem trazer qualquer PII, e nenhum dos 13
> tipos serve. Tipá-los `texto` seria **claro por DECLARAÇÃO** — pior que claro por omissão,
> porque parece decidido. *(Precedente que o censo apenas CONTA, não introduz:
> `journey.parecer` e `journey.resultado` já estão no mapa como `texto`.)*
>
> Dois casos que não são conteúdo livre e mesmo assim não têm tipo: **`session.preview`** —
> cujo valor é uma **spec de mascaramento** (`{"numero_cartao": "last_4", …}`), política
> guardada como dado — e **`session.reviewer_id`**, identidade de **usuário da plataforma**,
> para a qual nenhuma das 5 classes LGPD do catálogo foi pensada.
-->

#### D9.6 — Tipagem e mascaramento resolvem-se no CADASTRO

Decisão do dono. O catálogo de tipos (V2/V2b/D8) **sobrevive inteiro** e vira o vocabulário do
`atributo` de cada registro. E há uma consequência que barateia tudo:

> **O escritor não declara nada.** O tipo já está no cadastro; o caminho de escrita
> **CARIMBA** o `atributo` na entrada a partir do registro.

Três propriedades vêm juntas: o autor **não tem como errar** (não há o que declarar); o dado
guardado fica **autodescritivo** (bom para o snapshot durável da F5 e para export de LGPD);
e o tipo tem **uma casa só**.

⚠️ **Pré-requisito nomeado, e é o custo real:** carimbar exige **choke point de escrita**, e o
§1.7 já mediu **12 `HSET` diretos** no ctx sem ponto único. Enquanto eles existirem, o carimbo
tem furo — e o furo é silencioso.

#### D9.7 — A visibilidade do pool é SELEÇÃO sobre a estrutura cadastrada

Decisão do dono. A tela de configuração do pool **mostra a estrutura já cadastrada** e o
operador **seleciona quais campos são liberados** para os recursos daquele pool. Não há texto
livre, e não há como escolher o que não existe.

O mecanismo da **D6 sobrevive** — ela já trocou texto livre por seleção sobre o mapa. O que
muda é a **fonte**: passa a ser o cadastro, e a seleção pode ser por **campo** ou por
**atributo/classe**. A segunda é a que torna a config operável: hoje a D6 lista **113 tags
nominais**, o que ninguém mantém; classes são um punhado.

E isto é o que fecha a §3 do briefing do dono: o campo de config já existia com esse
propósito e não era usado, porque não havia estrutura declarada sobre a qual escolher.

#### D9 — FATIA 1 EXECUTADA (2026-08-30): os 37 cadastrados no mapa vigente

A migração começou pela parte que **não depende de a D9 ser aceita**: cadastrar no
`DEFAULT_CONTEXT_MAP` (o mecanismo da V3) os nomes que o censo mediu. É reversível,
não tem código novo e é o que leva o `unknown` da auditoria a zero — o número que a
V4 espera, na definição velha e na nova.

**75/53 → 94/82 canônicas/aliases; 37 não cobertos → 4.** Gate
`probe_context_map_audit.sh` **vermelho antes de verde** (o ramo B acusou a config
viva ainda em 75 até o `PUT` pela API oficial).

**Três reduções vieram da própria lista, e nenhuma é estética:**

1. **`survey_*` × `surveyed_*` fundidas.** Eram duas canônicas para UM fato — *qual
   segmento/agente está sendo pesquisado* —, escritas em sessões diferentes (bridge
   no `on_human_end` da ORIGEM × gateway no `collect_engage` da PESQUISA). Viraram
   dois aliases da mesma canônica, como `caller.cpf` × `session.cpf`. É a D9.8
   acontecendo: manter as duas daria, em seis meses, duas casas defensáveis para a
   mesma pergunta.
2. **Nove canônicas de `survey` estavam SEM `legado` desde a V3** — a grafia viva é
   PLANA (`session.survey_form_id`) e caía em `unknown`. Mesmo defeito que o
   cabeçalho do domínio `workflow` já documentava; a V3 declarou a canônica e deixou
   a grafia real órfã. **Por-catch da mesma classe:** `session.contact_outcome` e
   `session.max_rounds`, que o censo contava como *lidos sem escritor* e que também
   eram canônica declarada sem a grafia viva.
3. **O pacote de aprovação não era domínio novo.** `title`/`summary`/`status`/
   `approval_threshold` chegam pelo MESMO `delegate.context` que já depositava
   `dialog_form_id` e `decisions` — a V3 declarou metade do payload.

**Duas escolhas de tipo que são MEDIÇÃO, não conveniência:**

* `session.summary` é `texto` porque é lido em `DialogFormRenderer.tsx:232`
  **através da porta de masking**: tipo restritivo APAGA a tela de aprovação — o
  *"troca vazamento de PII por quebra muda de UI"* que o pré-requisito da V4 nomeia.
  O YAML já carrega a contramedida por escrito (`skill_limite_processo_v1.yaml:39-42`):
  o summary carrega só texto público, e cartão/CPF/valor viajam como tags SEPARADAS
  justamente para ter política própria.
* `session.contato.pergunta_coleta` é `texto` porque o valor é **exibido ao
  cliente** — mascará-lo quebra a coleta, não protege ninguém.

⚠️ **Uma departura declarada.** A nota que precedeu a fatia mandava **listar** ao
dono qualquer campo que pedisse domínio NOVO, e nomeava o copiloto. As cinco tags do
copiloto foram **declaradas**, e o motivo é que a nota não as alcançava: elas já são
escritas em `escopo.dominio.campo` pela própria plataforma (`copilot_emitter.py`,
`server.ts:2031-2034`), então declará-las **não escolhe nome nenhum** — zero alias,
zero taxonomia. O que a nota protegia era *inventar*; deixar tag canônica da própria
plataforma em `unknown` seria o defeito, não a prudência. Mesma leitura para
`session.processo`, que é o `journey.processo` já existente **um escopo abaixo**, e a
D2 impede que sejam alias um do outro (o escopo é o primeiro segmento). A decisão
aberta **#3** renormaliza os 15 domínios de uma vez quando cair; nenhum destes
acrescenta dívida que os outros já não tenham.

---

#### D9.8 — Domínio: lista FECHADA de PAPÉIS; vocabulário de negócio é DADO

O agrupamento existe porque a alternativa está medida: **130 grafias**, duas famílias de nome
para o mesmo domínio (`session.survey_*` do gateway × `session.surveyed_*` do bridge), um
namespace `approval.*` que não é escopo, e palavras genéricas viradas chave de topo
(`session.title`, `session.status`, `session.summary`).

**Mas a lista tem de ser um nível mais abstrata que o catálogo do tenant:**

```
produto.tipo = "seguro_auto"     ✅  o catálogo do tenant é VALOR
produto.seguro_auto.apolice      ❌  o catálogo do tenant virou ESTRUTURA
```

É a D8.2 um andar acima: o discriminador não sobe para a estrutura. Uma telco não tem
"seguro"; uma seguradora não tem "linha". Fechar o vocabulário de negócio no cadastro da
plataforma repete, um nível acima, o erro de fechar o mapa de nomes.

**Corolário de composição:** a lista é só de **papéis**. Misturar entidade (`cliente`,
`produto`) com estado/evento (`pendências`, `erros`) na mesma lista de topo é como taxonomia
apodrece — em seis meses *"pendência de cliente"* tem duas casas defensáveis, que é o defeito
de *"duas respostas para a mesma pergunta"* que este arco inteiro persegue. Estado é
`atributo` ou valor.

Candidatos: `cliente · produto · transacao · atendimento · processo · tarefa · sistema`.

⚠️ **A evidência a favor desta lista é FRACA, e está declarado.** Classificando as 130 grafias
medidas: **105 caem em uma só · 0 ambíguas · 10 fora · 15 ruído**. **O único número que vale
independente da população é o ZERO de ambiguidade** — ele é propriedade da lista, não dos
dados. Os outros descrevem hábitos de **demo**: o produto não está em produção e não há caso
real. A lista deve ser julgada por ser **principiada**, não por caber nos demos.

#### E três eixos, que é o teto

| eixo | responde | quem decide |
|---|---|---|
| **escopo** (`session`/`journey`/`customer`) | onde mora, por quanto tempo | roteamento que já existe |
| **atributo** (classe) | que espécie de dado é | catálogo fechado, plataforma |
| **domínio** | sobre o que é | cadastro |

O domínio carrega **organização e descoberta — nunca política**. Se ele começar a decidir
máscara, passam a existir duas respostas para *"isto é sensível?"*, e a permissiva vence.

#### O que esta emenda supersede, e o que sobrevive

**Supersede:** a **D2** enquanto *allowlist de leitura por nome* (o mapa vira o **cadastro**, e
isso é promoção, não descarte) e a **D5**, cujo deny-by-default deixa de ser sobre *nome não
declarado no read* e passa a ser sobre *tag não cadastrada no publish*.

**Sobrevive intacto:** a **D1** (o tipo é a declaração única), o **catálogo** (V2, V2b, D8), a
**D3**/**D4**, o mecanismo de **alias** da D3 — que deixa de ser caminho crítico e vira
**instrumento de migração** —, e a **D6**, que ganha a fonte que lhe faltava.

**A V4 é REDEFINIDA**, não adiada: deixa de ser *"inverter o default de leitura"* e passa a ser
*"ligar o portão de publish"*, com a lista de migração produzível por análise estática.

#### Decisões que esta emenda deixa ABERTAS

1. ~~**O choke point de escrita** — desenho e migração dos 12 `HSET` diretos.~~
   **DESENHO DECIDIDO em 2026-09-02** (ALW-02); a migração segue aberta. A pergunta era *um
   funil ou dois?* — o funil que existe é TS e 20 dos 21 sítios são Python. Escolhida a
   **declaração pura compartilhada + duas implementações finas + gate comparativo**, e a
   medição é o que a tornou barata: o mapa **já viaja como JSON** pelo config-api (nada a
   espelhar; só ~42 linhas de função pura precisam de gêmeo), e **4 dos 5 serviços Python
   já falam com o config-api** — o único que não fala tem 1 sítio. As duas alternativas
   caíram por medição: gêmeo solto reintroduz a cópia divergente que este arco persegue, e
   chamar o funil por rede poria 20 sítios de caminho quente contra a porta 3100, que saiu
   da LAN em 2026-09-01 (CAP-13) por servir transporte anônimo.
2. ~~**Onde mora a tela de cadastro**~~ — **DECIDIDA em 2026-09-02 (ALW-03): divisão por
   FATO, não por tela.** Ao medir, os dois critérios desta linha **discordavam**: *"o
   cadastro precisa morar onde o autor já está"* aponta para o editor, e a analogia da
   própria D9 (*"como um DialogForm"*) aponta para Configuração, admin-only. A analogia não
   transfere neste eixo — um DialogForm é conteúdo de negócio, um campo de ContextStore é
   declaração técnica que o flow precisa, e os autores são pessoas diferentes.

   O desempate foi ver que **"cadastrar um campo" são dois fatos com donos diferentes**:

   | fato | dono | onde |
   |---|---|---|
   | o **catálogo de tipos** — o que `cpf_br` mascara, para quais papéis, sua classe LGPD | compliance | `/config/masking`, grant `config.masking` |
   | o **mapa** — quais campos existem e qual tipo cada um usa | quem AUTORA flow | `/config/context-map`, grant **`config.context_map`** |

   Mesma forma do split `config.users` × `config.permissions`: *um rótulo com "e"
   provavelmente são dois fatos*. Medido antes de decidir: `skill_flows.operacao` e
   `.editar` nascem para admin **+ developer**; `config.masking` só para admin — logo o
   cadastro estava, por construção, fora do alcance de quem autora.

   **O autor ESCOLHE o tipo, do catálogo fechado** (decisão do dono). Ele pode dizer que um
   campo é `cpf_br`; não pode criar tipo nem mudar o que um tipo significa. Escolher `texto`
   para um campo sensível é possível, e o próprio ADR já licenciou isso na depreciação da
   D9.5: o contrato da §1.1 é que **alguém decidiu**, explicitamente e de forma auditável —
   *"garantir que a decisão seja BOA nunca foi o contrato"*.

   Gate: `infra/test/probe_context_map_grant_split.sh` (8 ramos; o **G** é ao vivo e tem
   controle POSITIVO obrigatório — sem ele um split que proibisse tudo passaria).

   ⚠️ **A outra metade desta decisão segue aberta**: *"o erro de publish precisa dizer
   exatamente o que registrar"* depende da V4, que é a ALW-01. E a afordância no editor
   (mostrar as tags não cadastradas do flow aberto) é a **ALW-08** — hoje o editor é um
   Monaco de YAML sem noção nenhuma de context tag.
3. ~~**A lista de domínios**~~ — **FECHADA em 2026-09-02 (ALW-04). E a advertência desta
   linha não era teórica: já tinha acontecido.**

   Medido antes de decidir: dos **24 domínios** declarados, **7 (20 campos, 21 aliases) eram
   vocabulário de demo** — `cartao`, `conta`, `portabilidade`, `processo`, `reembolso` —, que
   entraram no seed da plataforma pela FATIA 1 da D9. A plataforma distribuía `reembolso`
   para toda instalação.

   **O critério é uma pergunta só:** *de que o domínio FALA — da plataforma, ou do negócio do
   tenant?* `queue`, `pool`, `workflow`, `hook`, `wrapup` falam da máquina e existem em
   qualquer vertical; `reembolso` fala do negócio de um tenant.

   | categoria | domínios | campos |
   |---|---|---|
   | maquinaria da plataforma | 15 | 58 |
   | negócio UNIVERSAL (`cliente`, `contato`) | 2 | 16 |
   | **específico do tenant — SAIU** | **7** | **20** |

   Declaração: **24 → 17 domínios, 94 → 74 campos, 116 → 95 aliases**. Os 20 campos
   **continuam no mapa vivo** — mudaram de dono, não de existência: são cadastro do tenant
   (a tela da ALW-03), e o `probe_context_map_audit` passou a medir a config viva por
   **CONTENÇÃO** (viva ⊇ declarada), contando o excedente em vez de reprovar. Exigir
   igualdade obrigaria a plataforma a redistribuí-los, que é o que esta decisão proíbe.

   Mecanismo: `PLATFORM_CONTEXT_DOMAINS` em `@plughub/schemas` — a lista, com o critério
   escrito onde alguém edita o mapa — imposta por `verifyContextMap`
   (`non_platform_domains`) e reprovada pelo ramo A do gate.

   ⚠️ **`journey` ficou VAZIO na declaração**, e isso é medição: os dois domínios que ele
   tinha eram do fluxo de demo de limite. A plataforma não declara nada em escopo de journey.

   ⚠️ **A metade dos UNIVERSAIS está BLOQUEADA.** A decisão do dono foi mover `cliente` e
   `contato` para `core.customer.*` (90 d, a retenção certa para cadastro). Medindo o
   caminho: o hash do cliente existe **só no SDK**, nenhum serviço de produção o escreve; o
   funil TS roteia por prefixo **hardcoded**, então `core.customer.*` cairia no hash da
   SESSÃO em silêncio; e o funil Python **recusa** escopo não-sessão. É a **ALW-09**.
4. **O destino de `linha_em_servico`**, criado hoje a partir de um fluxo de demonstração: a
   distinção finalidade × cadastro é boa; o tipo pode estar sobre-ajustado.
5. **Classe LGPD para não-cliente** — `session.reviewer_id` é identidade de USUÁRIO da
   plataforma, e nenhuma das 5 classes foi pensada para isso. Lacuna de classe, pequena e
   real. *(Sobreviveu à depreciação da D9.5, onde estava só coabitando.)*
6. **`session.preview`** — política de mascaramento guardada como valor de tag. Decidir se
   isso continua morando no ContextStore antes de lhe dar um tipo.

> ⚠️ A antiga #5 (*marcação de conteúdo livre*) **saiu**: a D9.5 foi depreciada pela própria
> D9 no mesmo dia — ver acima. Nenhuma decisão de mecanismo bloqueia a migração.

## 3. As sete perguntas do briefing — respostas

**1. As regras vêm mesmo do config-api?** ✅ Sim, e a tela é que mentia — **fechado em 2026-08-26**.
A causa **não** era o `??` de `MaskingPage.tsx:160` (que nunca chegava a ser exercido): era colisão
de rota no proxy do platform-ui. Detalhe e lições no `CHANGELOG.md` § V0. Fica o método: a hipótese
herdada apontava um passo **adiante** do defeito, e só caiu porque foi medida antes de consertada.

**1b. Quem é a fonte de verdade entre seed e config viva?** → **D7**: config-api. O seed compara e
loga — ✅ **entregue 2026-08-29** (a metade do seed; a proveniência na tela segue aberta).

**2. Como declarar ~40 tags sem virar formulário impraticável?** → **D1 + D2**: a allowlist é **por
campo**, mas o que é reusável é o **tipo**, não a regra. Declarar um campo é escolher um tipo de uma
lista curta. `segment.{uuid}.*` é o caso que não cabe em caminho exato e precisa de **nó com segmento
variável** no mapa (`segment.{id}.wrapup.resumo`) — o matcher atual não entende, e isto é trabalho
declarado da V3, não surpresa.

**3. Quem semeia a allowlist inicial?** → O mapa **não nasce vazio**: é gerado da varredura do
ContextStore vivo + das 23 regras + do seed, e revisado. A varredura vê só o que o ambiente
exercitou — por isso a semente não basta sozinha e a **V3 (modo auditoria)** existe.

**4. O default é `hidden` ou `full`?** → **Deixa de ser uma escolha global.** É propriedade do tipo,
decidida uma vez por tipo. Para campo funcional (`dialog_form_id`, tipo `texto`) nenhum dos dois
ocorre — ele é declarado e visível. Para o **campo não declarado**, o default é `hidden` **contado**
(D5): `full` mostraria `***` para algo cuja existência não foi declarada, o que é informação sobre um
campo que não deveria estar ali.

**5. Vale um modo de auditoria antes de virar a chave?** → **Sim, e é fase própria (V3).** Roda
deny-by-default em modo "só registra o que teria sido escondido", produzindo a lista real sem quebrar
ninguém. É a versão falseável do que hoje seria adivinhação.

**6. O escopo inclui a persistência (F5)?** → **Sim para o vocabulário, não para o portão** (D4). A
máscara de valor da persistência passa a vir do tipo; o portão de namespace continua **fora** do
registro durável, pelo motivo já escrito em `server.ts:1206-1217`.
⚠️ E a pergunta cresceu: não são dois consumidores, são **quatro** (§1.5 achou uma porta a mais).

**7. `journey.*` merece política própria?** → **Sim, e ela cai fora do texto: vira o escopo `journey`
no mapa** (D2). Um campo de processo atravessa N contatos e vive 30 dias; declarar o escopo na chave
já o separa. O `session.delegate_resume_token` (11 sessões, tipo `token`, `hidden`) é o caso que
prova a necessidade: capacidade num store durável.

---

## 4. Alternativas consideradas

**(a) Manter globs de sufixo como mecanismo principal.** Recusada: protege pelos tipos que por acaso
conhecemos; um campo com nome novo nasce exposto. Os globs **permanecem** — mas rebaixados a **rede
de segurança** para campo declarado sem tipo, não como política.

**(b) Catch-all `session.* → hidden`.** Recusada com evidência escrita no próprio seed
(`seed.py:507-509`): derruba `session.dialog_form_id`/`session.decisions` e a tela de aprovação para
de renderizar **em silêncio**.

**(c) Hierarquia de negócio na raiz da chave (`cliente.cpf`), escopo declarado no nó.** Recusada —
§1.6 e D2: cria um segundo roteador de storage dependente de config, com degradação silenciosa no
caminho de retenção de PII.

**(d) Dois eixos (categoria abstrata × formato).** Superada por D1: o tipo funde as três metades em
vez de organizá-las, e `lgpd` cabe como propriedade.

**(e) Alias permanente (as duas grafias válidas para sempre).** Recusada: é *"duas grafias da mesma
coisa"*, que o `CLAUDE.md` e o histórico de passagens registram como forma de divergência. Aceita
apenas como **migração contada** (D3).

**(f) Armazenar o ContextStore como JSON aninhado.** Recusada: o hash com um campo por caminho é o
que dá escrita atômica por campo, `ttl_override_s` por campo, `confidence`/`source`/`visibility`/
`updated_at` por campo, e as três estratégias de merge. Aninhar obriga read-modify-write do documento
e mata as quatro. **A hierarquia é da declaração, não do armazenamento** — e o caminho já é
hierárquico hoje (`ContextTagEntrySchema.tag`, regex multi-nível em `context-store.ts:115`).

---

## 5. Consequências

**Aceitas:**

- **Campo novo exige declaração antes do primeiro uso.** Hoje um autor escreve
  `context_tags: {tag: "session.novo_campo"}` e funciona; depois, não funciona até declarar. É atrito
  real e permanente — o preço da inversão. Atenuantes: modo auditoria (V3) avisa antes de bloquear;
  omissão contada (V1) nomeia o que falta; e o mapa **torna autocomplete possível no editor de
  fluxo**, o que troca um typo silencioso (`caller.telefonе`) por validação no ato.
- **Formatação é render-time, nunca storage.** Gravar `R$ 1.234,56` corromperia o valor que o agente
  passa ao CRM. O hash guarda o canônico — mesma regra que já vale para máscara.
- **Ordem máscara × formato é declarada, não emergente.** `applyMaskingTypeToValue` já faz
  `raw.replace(/\D/g,"")` antes do `last_4` (`server.ts:1058`), isto é, hoje a máscara come a
  formatação. Regra: **máscara opera no canônico; formato só se aplica quando a máscara é ∅ ou
  `plain`.**
- **Validação na escrita começa LOGANDO, nunca recusando.** Recusar é mudança de comportamento com
  modo de falha em agente rodando.
- **Os 12 `HSET` diretos (§1.7) não passam a ser bloqueados pela V-inicial.** Eles entram no modo
  auditoria como qualquer outro escritor; convertê-los a um choke point é trabalho próprio, e o ADR
  o declara em vez de presumir que já estão cobertos.

**Riscos:**

- **Máscara faltando é invisível.** Ninguém abre chamado por ver `***` a mais. Toda fase que mexe em
  máscara precisa de teste que prove que o **supervisor CONSEGUE ver em claro**, não só que o
  operator não consegue (`server.ts:1119-1121` documenta a vez em que isso passou despercebido).
- **`admin` é `supervisor_role` por default** e pula o portão inteiro. Comparar duas telas de admin
  não julga nada, e comparar admin × operator numa sessão sem PII também não. Discriminador pronto:
  `infra/test/probe_context_visibility.sh`, que **injeta** `caller.nome` (ns barrado) +
  `caller.customer_id` (allow-tag).
- **Cache de 60 s no mcp-server** (`server.ts:910`): medir antes disso lê a política antiga e parece
  que a escrita não pegou.

---

## 6. Fases

| fase | entrega | reversível |
|---|---|---|
| **V0** | Consertar a tela que mente. **Metade FEITA em 2026-08-26** (ver `CHANGELOG.md`): a causa não era o `??` — era **colisão de rota no nginx** (`masking` é nome de página *e* namespace; `location` casa a URI sem a query string, então o `fetch` recebia `index.html` com HTTP 200). Discriminador passou a ser o `Accept`. Gate `probe_config_route_collision.sh`, visto vermelho antes de verde. **Metade ABERTA:** colapsar os inventários de categoria de §1.3 — depende da V2 | sim |
| **V1** | A omissão deixa de ser muda. **FEITA em 2026-08-26** (ver `CHANGELOG.md`): `MaskedContextView` com `total` + `by_rule` + `by_pool_scope`, `context_withheld` no endpoint, faixa na aba Contexto. **Duas listas** porque as causas se consertam em telas diferentes. Gate `probe_context_withheld.sh` (6 ramos; D = testemunha negativa, F = aritmética) | sim |
| **V1b** | A segunda porta (§1.5). **FEITA em 2026-08-26** (ver `CHANGELOG.md`): política extraída para `lib/context-masking.ts` e aplicada ao tool MCP `supervisor_state`, em **grau operator sem portão de namespace**; `context_masking` no retorno. Gate `probe_supervisor_tool_masking.sh` (6 ramos; **C** = testemunha negativa contra blanket-mask, **B** usa o endpoint HTTP como **oráculo** em vez de valor hardcodado). Barata porque **0 skills consumiam** o campo — o custo teria crescido com o primeiro consumidor | sim |
| **V2** | Catálogo de tipos (D1) declarado e semeado, **sem nenhum consumidor novo** — os mecanismos atuais passam a lê-lo. **FEITA em 2026-08-26** (ver `CHANGELOG.md`): `DataTypeSchema` + `DEFAULT_DATA_TYPE_CATALOG` (7 tipos, só o ALCANÇÁVEL — `iban`/`passport` fora), `DEFAULT_MASKING_RULES` passou a ser **derivada** do catálogo, `masking.types` semeado, e a tela iterando o dado com **selo derivado** em vez do "Ativo" incondicional. Gates `probe_type_catalog.sh` (dois lados + testemunha do ORÁCULO) e `probe_masking_display_parity.sh` (três portas, com testemunha contra o caso vácuo). Dois defeitos alheios caíram junto: o leitor de regras de canal apontava para uma rota inexistente (**inerte desde sempre**) e o `\(?` do regex de telefone era ramo morto | sim |
| **V2b** | Fechar a casa LEGADA de display rule (`rule.{category}`) — pré-requisito do `masked` TIPADO, que não pode ser escrito enquanto a mesma pergunta tiver duas respostas. **FEITA em 2026-08-29** (ver `CHANGELOG.md`). **A remoção foi autorizada por CONTADOR, não por decreto:** o ramo D do `probe_type_catalog.sh` publicava o número de chaves legadas dizendo *"a remoção é MEDIDA por este número zerar"* — zerou. Medido antes de tocar: **zero escritores** em todo o repositório (a V2 já migrara a tela; os três `putConfig` da `MaskingPage` são `audit_policy/{key}`, `masking/types`, `masking/context_rules`), **zero chaves** em todo o `platform_config` (todos os tenants, todos os namespaces — contra 8 linhas do ns `masking` de testemunha) e **quatro leitores**, todos no `platform-ui`. Gate `probe_legacy_display_rule_closed.sh`, visto **vermelho antes de verde** (4 casas → 0). Achado: o leitor legado **não era peso morto** — `getMaskingRule` devolvia o override e `update()` o gravava de volta no CATÁLOGO, então editar qualquer campo de uma categoria com override **promovia o legado a tipo, em silêncio**; armadilha ARMADA, blast radius zero por ausência de dado | sim |
| **V3** | Mapa do ContextStore (D2) + aliases contados (D3) + **modo auditoria**. **FEITA em 2026-08-29** (ver `CHANGELOG.md`): `masking.context_map` com **74 campos e 39 aliases**, semeado a partir do CENSO (leitura ∪ escrita — as duas varreduras discordam), `resolveContextTag` resolvendo **na borda** nas DUAS portas humanas, e o PAR de contadores com data em `{t}:ctx:audit:*`, legível por `GET /internal/context-audit`. **O mapa não recusa nada e o enum `mode` tem UM valor** — não existe config capaz de ligar a V4 antes de o código que a honra existir. Três achados que a fase produziu: **(1)** o `486` da §1.8 **não reproduz** (são 231/53 arquivos), e leitura sozinha não é o denominador — escrita traz campos que nenhuma leitura menciona; **(2)** `legado` teve de virar ARRAY (D3), porque o mesmo campo tem duas grafias vivas; **(3)** o mapa exigiu o primeiro tipo que **não mascara** (`texto`), e com ele um fail-open que não existia — `masked: "texto"` passaria pelo portão da T5, que só conferia EXISTÊNCIA do id. Fechado por predicado derivado (`typeMasksSomething`), não por lista de exceção, com testemunha positiva ao lado. Gate `probe_context_map_audit.sh` (8 ramos), visto **vermelho antes de verde**. ⚠️ **Números superados duas vezes no mesmo dia: pela D8 (75/40) e pela FATIA 1 da D9 (94/82)**, com o catálogo em **13** tipos | sim |
| **V4** | ~~Inverter para deny-by-default, com a lista real que a V3 produziu~~ — **REDEFINIDA pela D9 (2026-08-30)**: deixa de ser *"inverter o default de LEITURA sobre uma lista de nomes"* e passa a ser *"ligar o PORTÃO DE PUBLISH sobre o cadastro"*. Não é adiamento: a versão antiga estava bloqueada por uma enumeração que não fecha (4 fluxos → 13 correções + 7 pendências), e a nova tem lista de migração **produzível por análise estática** — medido: 21 escritas em YAML, **todas literais**, zero nome dinâmico | **não** |
| **V5** | Tela do pool vira seletor (D6); fechamento dos aliases cujo contador zerou. **D6 FEITA em 2026-08-29** (ver `CHANGELOG.md`): `context_visibility` deixou de ser texto livre e passou a SELEÇÃO sobre os nós do mapa, servida por `GET /v1/context-map/visibility-options` (5 namespaces, 113 tags). Medido ao fazer: havia **QUATRO** cópias da afirmação sobre o default e as quatro discordavam — o conserto de 08-26 arrumou a dica e **não tocou no placeholder logo abaixo**, que é exatamente o *"sem mecanismo que impeça a volta"* previsto aqui. E **dois dos sete namespaces da taxonomia não existem** (`service`, `history`: zero produtores, zero no store vivo), com `service` no default da plataforma. O seletor os elimina sem lista de exceção. Duas propriedades load-bearing: valor LEGADO fora do mapa é mantido e MARCADO (descartá-lo no primeiro save seria mudança de política silenciosa, na direção que ninguém percebe), e a LIMPEZA passou a existir — não havia caminho nenhum, em três camadas mudas (tela omitia a chave, schema sem `.nullable()` ⇒ 422, `PUT` passando `null` cru a coluna `Json` que exige `DbNull`). Gate `probe_context_visibility_selector.sh` (6 ramos). **O fechamento dos aliases segue pendente e é BLOQUEADO POR TEMPO**: depende do contador da V3 zerar por N dias | sim |

Ordem inegociável: **V1 antes de V4** — e, desde a D8.3, **catálogo antes do mapa**: campo cujo tipo
não existe fica FORA, e a lacuna fecha-se no catálogo. Declarar um tipo aproximado para o campo caber
escreveria no mapa uma política que ninguém decidiu, e a V4 a aplicaria.

---

## 7. Como isto pode ficar vermelho

Um ADR cuja decisão não pode reprovar é a mesma família dos testes que este repositório cataloga.
Gates exigidos por fase:

- **V0** — o número de regras na tela tem de igualar o número que a API devolve, com **testemunha**:
  um tenant sem override tem de mostrar as globais, e um com override tem de mostrar a soma resolvida.
  Um contador só de presença passaria com a API vazia.
- **V1** — testemunha negativa obrigatória: sessão **sem** campo oculto tem de reportar
  `hidden_count: 0` e **nenhum** nome. Um produtor que nunca emite passa em qualquer teste de presença.
- **V2** — todo tipo do catálogo tem de ser alcançável por algum campo, e todo campo tem de ter tipo
  existente. Os dois lados, senão volta `iban`/`passport`.
- **V3** — o contador de alias precisa de par: resoluções via alias **e** via canônica. Só o primeiro
  não distingue "ninguém migrou" de "ninguém usa".
- **V3** ✅ — o par existe e é lido junto: `GET /internal/context-audit` devolve `alias[]` e
  `canonical[]` com `count` + `last_seen` cada, mais `declared_in_map`. Medido no caminho real: uma
  leitura com `caller.cpf` (alias) e `session.pool.id` (canônica) move **os dois** contadores, e o
  ramo D do gate é a testemunha negativa nos dois sentidos — tag canônica não incrementa alias, e
  vice-versa. Sem isso o par passaria por um contador que incrementa tudo.
- **V4** — a medição que autoriza virar a chave é *"o modo auditoria acusou zero campos não
  declarados por N dias"*, com o número de campos **declarados** ao lado. Zero sobre zero é um serviço
  parado, não uma allowlist completa.
  > ⚠️ **A V3 deixou uma condição a mais, e ela não estava prevista aqui:** `unknown` só é confiável
  > enquanto `overflow == 0`. O balde tem teto (500 grafias distintas), porque `session.*` recebe
  > campo autorado pelo tenant e é ilimitado por construção. Truncar sem dizer faria a lista parecer
  > completa — exatamente o que a V4 não pode acreditar —, então o teto INCREMENTA `__overflow__` e a
  > leitura o publica ao lado. **`overflow > 0` invalida a autorização**, não a atrasa.
  >
  > E a auditoria observa a LEITURA, nas duas portas humanas: um campo escrito por um dos 12 `HSET`
  > diretos (§1.7) e **nunca lido** é invisível. Isso é benigno para esta decisão — o que a V4
  > inverte é a política **R-humano**, e campo que ninguém lê por essa porta não perde nada ao deixar
  > de ser acessível por ela —, mas deixa de ser benigno se a V4 alargar para W ou P.
- **Transversal** — nenhum gate pode sair `OK` com ramo `INCONCLUSIVO`. O
  `probe_chip_breakdown.sh` foi corrigido em 08-26 por ter exatamente essa forma; o
  `probe_f4_direction_and_classes.sh` **ainda tem**.

---

## 8. O que este ADR NÃO decide

- **Mascarar o que o agente lê** (`R-agente`) — permanece cru por design; o controle é permissão de
  tool + auditoria MCP, noutro ADR.
- **Migrar as 486 ocorrências** (§1.8) — a V3/V5 tornam a migração **medida por decaimento de
  contador**, não um big-bang; o cronograma sai do contador, não daqui.
- **Converter os 12 `HSET` diretos num choke point** (§1.7) — declarado como trabalho próprio.
- **A fatia C de `session:{id}:meta`** — a visibilidade é hoje lida do pool de **ENTRADA**, não do que
  atende: o `poolId` vem de `session:{id}:meta` em `server.ts:1636` e alimenta o portão em
  `:1654-1668`. Um contato transferido aplica a política do pool de ORIGEM. É consumidor da fatia C
  (`docs/guias/session-meta-ownership.md`) e o primeiro em que a acepção errada custa **exposição de
  PII** — mas o conserto é lá, não aqui.
- **`catch { /* non-fatal — use default */ }` sem log na leitura de `context_visibility`**
  (`server.ts:1667`) — "config não aplicada" e "config é o default" ficam indistinguíveis. Uma linha,
  cabe em qualquer toque futuro nesse bloco.

> ⚠️ **Correção de referência:** o `TODO.md` aponta estes dois defeitos em `server.ts:1421` e `:1452`.
> Medido nesta sessão: **as duas linhas estão obsoletas em ~215 linhas** — `:1421` é hoje um
> comentário sobre `dialogApiUrl` e `:1452` não é o `catch`. O arquivo deslocou — o endpoint
> `/internal/context-snapshot` da F5 entrou em `:1524`, acima dos dois. Números herdados foram
> conferidos, não copiados.


---

## Apêndice — resumo denso migrado do índice do `CLAUDE.md` (2026-08-31)

> Este bloco vivia como **uma linha** do índice `docs/` no `CLAUDE.md`, onde ocupava 15890 bytes.
> Medido antes de mover: **~85% do seu vocabulário já existe neste ADR** — ele é uma condensação
> independente, não uma cópia, e por isso os ~15% restantes (achados, números e nomes de arquivo que
> só foram registrados no índice) **não existiam em lugar nenhum além dali**. Movido inteiro, sem
> resumir, porque a alternativa — cortar no CLAUDE.md e confiar que o ADR já dizia tudo — perderia
> exatamente a fração que não dá para recuperar.
>
> **É trabalho aberto**, não documentação final: a fração nova deve ser dobrada no corpo do ADR e
> este apêndice, encolhido. Enquanto isso não acontece, ele é a única cópia.

ContextStore como ALLOWLIST: `default_unmatched_operator: "plain"` é **deny-nothing** (campo sem regra nasce em claro, e desde a F5 nasce DURÁVEL em claro), e a causa é estrutural — `session.*`/`journey.*` não podem ter catch-all (o seed avisa: derruba a tela de aprovação em silêncio) e é justamente ali que o `delegate.context` deposita. **TIPO é a declaração única** (formato × máscara-por-papel × classe LGPD; qualquer uma pode ser vazia) — funde as três METADES medidas: `MaskingRule`+regex tem detecção sem papel/canal, `MaskingDisplayRule` tem canal (`display_screen`×`display_voice`) sem papel, `ContextMaskingType` tem papel sem detecção/canal. **MAPA em `escopo.dominio.campo`** = a allowlist; o escopo FICA no primeiro segmento porque o prefixo hoje roteia hash+TTL em 3 casas (`sdk/context-store.ts:106-120` · `interpolate.ts:237` · `journey.ts:180`) e mover isso p/ config criaria roteamento de RETENÇÃO DE PII que degrada mudo. Legado vira `alias` no próprio nó, resolvido na BORDA (nenhuma regra escrita contra alias — seria "regra que não regra"), **contado e datado**. **QUATRO políticas, um vocabulário** (W escrita · R-agente · R-humano · P persistência) — fundi-las é erro (o portão de namespace na persistência apagaria história). **Pré-requisito inegociável: a omissão deixa de ser MUDA antes da inversão** (`continue` em `server.ts:1163`/`:1179`), senão troca vazamento de PII por quebra muda de UI. Achados que o ADR carrega: a 2ª porta — o tool `supervisor_state` devolvia o ctx **CRU** — **fechada na V1b** (política em `lib/context-masking.ts`, uma casa para as duas portas; tool entrega em **grau operator sem portão de namespace**, porque não há visualizador com PAPEL e o pool que ele tem à mão é o de ENTRADA) · ~~3~~ **SETE inventários de categoria discordavam** — a contagem herdada era por MENÇÃO e perdeu os três com produtor vivo em Python, que eram justamente os que podiam divergir sem teste de TS notar; medidos lado a lado, **nenhuma das 5 linhas era unânime** · **12 `HSET` diretos** no ctx sem choke point. Fases V0 (tela que mente) → V1 (contar, não omitir — vale sozinha) → V1b (2ª porta) → V2 (tipos) → **V2b (fechar a casa legada `rule.{category}`)** → V3 (mapa+alias+modo AUDITORIA) → **V4 inverter (não reversível)** → V5 (seletor no pool). Ordem inegociável: V1 antes de V4. **V0/V1/V1b/V2/V2b/V3, a METADE D6 da V5 e a METADE do seed da D7 entregues (ver CHANGELOG); a V4 é a próxima, e só o número da V3 a autoriza** — a outra metade da V5 (fechar aliases) depende do mesmo contador decair, logo as duas são bloqueadas por TEMPO, não por esforço. A **V3** semeou `masking.context_map` (**74 campos, 39 aliases**; **75 e 40** desde 2026-08-30) a partir do CENSO — e o censo corrigiu o denominador: o `486` da §1.8 **não reproduz** (são **231 em 53 arquivos**), e leitura sozinha não bastava, porque as declarações `tag:` dos YAML trazem campos que nenhuma leitura menciona. `legado` virou **ARRAY** (o mesmo campo tem duas grafias vivas: `caller.cpf` e `session.cpf`). O mapa exigiu o primeiro tipo que **não mascara** (`texto`) — e, em 2026-08-30, mais dois: `card_expiry` (o vencimento tinha política viva e nenhum tipo casava máscara **e** classe) e `linha_em_servico` (o primeiro cujo motivo de existir é a FINALIDADE, não o formato), fechando a lacuna que era **do catálogo** e só então deixando o campo entrar no mapa — **a ordem é o critério** e, com ele, fechou um fail-open que o portão da T5 abria — `masked: "texto"` passaria, porque aquele portão só conferia EXISTÊNCIA do id; hoje há predicado derivado (`typeMasksSomething`), com testemunha positiva. **O modo auditoria não esconde nada e o enum `mode` tem UM valor** — não há config que ligue a V4 antes do código que a honre. Dois avisos para quem for fazer a V4: `unknown` só vale enquanto `overflow == 0` (o balde tem teto, porque `session.*` recebe campo autorado pelo tenant), e a auditoria observa a LEITURA — campo escrito por um dos 12 `HSET` e nunca lido é invisível (benigno para R-humano, não para W/P). Achado de exposição REPRODUZIDO ao vivo: **`session.numero_atual` sai em CLARO** (não casa nenhuma das 23 regras; `*.telefone` exige o sufixo) — e o **desfecho foi o OPOSTO do previsto** (decisão do dono, 2026-08-30): aquele número **é o objeto do atendimento**, não dado de cadastro, e segue em claro **declarado** (`tipo: "linha_em_servico"`) em vez de por omissão; o telefone de CADASTRO (`session.cliente.telefone`) continua protegido. A §1.1 não muda de sentido: o defeito nunca foi o valor visível, foi o valor visível **porque ninguém decidiu**. Daí a regra que fica — **FINALIDADE é dimensão do TIPO, nunca exceção de regra**, nomeada pela finalidade e com a classe LGPD preservada (o que se declara vazio é a MÁSCARA, nunca a classe), senão mapa e regra dão duas respostas e a permissiva vence. Gate `probe_context_map_audit.sh` (9 ramos, vermelho antes de verde). **A D6 (2026-08-29)** trocou o texto livre de `pools.context_visibility` por SELEÇÃO sobre o mapa (`GET /v1/context-map/visibility-options`: 5 namespaces, 113 tags), e o que ela mediu generaliza: eram **QUATRO** casas afirmando o default e as quatro discordavam — o conserto de 08-26 arrumou a dica e **não tocou no placeholder três linhas abaixo**. **`service` e `history` não têm produtor** (zero em `packages/`, zero no store vivo) e `service` está no default, concedendo nada. Regra derivada: **enquanto a lista for ESCRITA, ela diverge; o conserto é derivá-la.** Duas propriedades load-bearing: valor legado fora do mapa é MANTIDO e marcado (descartá-lo no save seria mudança de política silenciosa) e a LIMPEZA passou a existir — não havia caminho, em três camadas mudas (tela omitia a chave · schema sem `.nullable()` ⇒ 422 · `PUT` com `null` cru em coluna `Json`, que exige `Prisma.DbNull`). Gate `probe_context_visibility_selector.sh` (6 ramos). **A D7 (2026-08-29)** fez o `seed` do config-api **comparar e logar** em vez de pular mudo — medido antes de mudar: **77 keys, 76 iguais, 1 divergente**, e dizer o número pequeno É a entrega (nas horas anteriores haviam nascido DUAS divergências e nenhuma se anunciou). **Compara e loga, nunca conserta**, porque a divergência tem DUAS direções: reaplicar acrescenta o que só está na declaração *e descarta o que só está no banco* — em `masking.context_rules` são 10 regras só no declarado e `session.cpf_titular` só no gravado, que nenhum glob cobre (`*.cpf` casa o SUFIXO), logo `--overwrite` cego derrubaria o campo para `plain`. ⚠️ **Medido em 2026-08-30: aquele campo NÃO TEM PRODUTOR** — o campo de tela saiu do formulário e foi substituído por `vencimento_cartao`, então a "divergência nos dois sentidos" é **uma política em dois momentos**, não duas políticas, e o `overwrite_would_drop = 1` conta uma regra morta. O número seguiu certo; a LEITURA dele é que exigia medir o produtor. Daí `overwrite_would_drop`, o único número que responde *"posso reaplicar sem perder nada?"*. Achado que reposicionou a fase: **a DECLARAÇÃO pode estar velha** — o `seed.py` DENTRO do container estava atrás do repo (imagem sem bind mount) e a 1ª medição deu 2 em vez de 1; `divergent=0` de imagem atrasada é afirmação sobre outro arquivo. Virou o ramo A do gate `probe_seed_drift_named.sh` (6 ramos, 4/4 mutações pegas — e a 1ª tentativa da mutação da testemunha negativa NÃO pegou, neutralizada pela segunda guarda do comparador). Aberta a metade da TELA (proveniência global × override por nó). A V2 fechou a metade aberta da V0 (catálogo único, `DEFAULT_MASKING_RULES` derivada dele, fantasmas fora, selo derivado) e derrubou dois defeitos alheios: o leitor de regras de canal apontava para rota inexistente (**inerte desde sempre**, 404 engolido por `.catch(() => {})`) e o `\(?` do regex de telefone era **ramo morto** (`\b` nunca casa antes de `(`). **A V2b é pré-requisito do `masked` TIPADO** — enquanto a dimensão CANAL tivesse casa própria, *"que máscara este campo usa?"* teria duas respostas e valeria a mais permissiva (o leitor legado **vencia** o catálogo). Três medições que regem o método: **(1)** a premissa herdada *"a tela ainda escreve o legado"* era **falsa** — as linhas citadas eram a declaração da função e duas LEITURAS; **(2)** o caso real não era nenhum dos dois ramos previstos, e sim **zero escritores + zero dados + 4 leitores**, o menor deles; **(3)** quem autorizou remover foi o **contador** que a V2 deixou publicando *"a remoção é MEDIDA por este número zerar"*, não uma decisão nova. Achado: o leitor legado **não era peso morto** — `getMaskingRule` o devolvia vencendo o catálogo e o `update()` da tela o gravava de volta NO catálogo, promovendo legado a tipo em silêncio (armadilha ARMADA, blast radius zero por ausência de dado). Gate `probe_legacy_display_rule_closed.sh` (fonte · oráculo · config viva · store inteiro), vermelho antes de verde; a **exclusão de linha de comentário é load-bearing e não higiene** — depois do conserto **3 linhas ainda casam a regex e as 3 são a prosa que documenta a remoção** — **Aceito, parcialmente implementado** (V0 metade · V1 · V1b · V2 · V2b · V3 · **D8** · V5 metade/D6 · D7 metade/seed; V4 não iniciada). A **D8 (2026-08-30)** acrescentou a dimensão que faltava ao tipo — **FINALIDADE** — e o critério de nomeação do mapa: campo cuja finalidade dispensa máscara ganha tipo PRÓPRIO (`linha_em_servico`), nunca regra `plain` de exceção; o discriminador mora no **domínio** (`cartao.cpf`), porque o casador **não tem glob de meio** e um `*cpf*` seria regra inerte; e a lacuna de tipo fecha-se no **catálogo** antes de o mapa crescer (`card_expiry`). Catálogo 11→**13**, mapa 74/39→**75/40** (e **75/53** depois dos 13 aliases derivados do censo de produtores; **94/82** desde a FATIA 1 da D9). **A D9 (proposta 2026-08-30) SUPERSEDE a D2 como allowlist-por-nome:** o ContextStore vira **CADASTRO** — domínio inteiro configurado antes do uso, como um `DialogForm`; o que não está registrado **não passa no PUBLISH** (runtime nunca rejeita: resolve restritivo e LOGA, mesma dupla postura da D3). Tipagem e máscara resolvem-se no cadastro, o escritor **não declara nada** (o caminho de escrita CARIMBA o `atributo`, o que exige o choke point que hoje não existe — 12 `HSET` diretos), e a visibilidade do pool vira **seleção sobre a estrutura cadastrada**. O motivo é medido: a enumeração de NOMES não fecha (75 canônicas declaradas × **4** observadas escrevendo; 4 fluxos de 44 skills geraram 13 correções), enquanto o catálogo de TIPOS fecha. A pré-condição do modelo foi medida: **21 escritas em YAML, todas literais**, zero tag com nome dinâmico ⇒ a população é **estaticamente enumerável**, e o problema deixa de ser de observação (rodar tráfego até secar) para ser de análise estática. **A V4 é REDEFINIDA**, não adiada. **O censo foi feito em 2026-08-30** ([`docs/product/contextstore-cadastro-censo.md`](docs/product/contextstore-cadastro-censo.md)) e confirmou a premissa — **91 nomes escritos, ZERO dinâmicos**, 54 já cobertos, **37 a cadastrar** —, mas com duas emendas que mudam o desenho. **(1) O extrator NÃO é um walker de YAML**: a D9 contava duas superfícies de autoria e são **SEIS**, quatro invisíveis à árvore (`context_json` é string JSON; `context_set`/`context_write` guardam o nome em `input.tag`; `delegate/collect.context` têm o prefixo `session.` composto NO GATEWAY, logo o nome final não está no arquivo) — portão escrito da forma óbvia fica verde com quatro superfícies por baixo, **fail-open por invisibilidade**. **(2) São TRÊS origens, não duas**: o corpo HTTP do webhook escreve cada chave verbatim e sem prefixo (`webhook.py:630`), e é daí que vêm as duas únicas tags sem namespace (`campaign_id`, `target_pool`); nenhum portão de publish a alcança, sobra a postura de runtime. **O número que dimensiona a decisão é 10, não 37** — 27 são identificador/enum (`texto`, sem política) e 10 pedem escolha de tipo. **A D9.5 foi DEPRECIADA pela própria D9**, no mesmo dia: a versão anterior desta linha dizia que 8 dos 10 *"pedem capacidade que o catálogo não tem"* e promovia a D9.5 a pré-requisito — **falso**, porque `ContextMapFieldSchema.tipo` é `z.string()` validado contra o catálogo e o mapa aceita qualquer um dos 13 tipos, `opaque` inclusive ⇒ **todo campo tem onde ser cadastrado**. Pior, o mecanismo que a D9.5 invocava **não existe**: `formato.detect_pattern` tem **zero consumidores**, e o motor real (`MaskingService.applyMasking`) roda num sítio só (`tools/session.ts:472`), sobre **mensagens do stream**, nunca sobre valor de ctx. É uma sub-decisão superada pelo PRÓPRIO PAI — foi escrita a partir da intuição pré-cadastro, e sob o cadastro escolher `texto` para um campo de prosa é **decisão explícita e auditável**, que é todo o contrato da §1.1 (*o defeito era o valor visível PORQUE NINGUÉM DECIDIU*; garantir que a decisão seja BOA nunca foi o contrato). Sobrevive só o estreito: declarar tipo em prosa é APOSTA e não descrição, o que muda o que a TELA de cadastro diz a quem escolhe — afordância, não mecanismo. **A migração da D9 não tem bloqueio.** Validação cruzada: os 7 `unknown` que o tráfego acusou estão todos na lista, que é superconjunto estrito por ~5×. **A FATIA 1 foi EXECUTADA em 2026-08-30** — os 37 cadastrados no mapa vigente (**75/53 → 94/82**), `não cobertos` **37 → 4**, e a prova é ao vivo: cortada a série e rodado o `smoke_limite_tres_acessos`, os 7 `unknown` viraram 6 `alias` e sobrou **só `session.preview`**, que é justamente um dos quatro deixados de fora com dono (`preview` = decisão #6 · `reviewer_id` = decisão #5, lacuna de CLASSE no catálogo e a ordem é catálogo antes do mapa · dois ecos de **demo**, que não entram no seed da PLATAFORMA). Três reduções vieram da lista e nenhuma é estética: **`survey_*` × `surveyed_*` fundidas** (duas canônicas para UM fato, escritas em sessões diferentes — bridge no `on_human_end` da ORIGEM × gateway no `collect_engage` da PESQUISA); **nove canônicas de `survey` estavam SEM `legado` desde a V3**, com a grafia viva PLANA caindo em `unknown` (mesmo defeito que o domínio `workflow` já documentava — canônica declarada, grafia real órfã; por-catch: `session.contact_outcome` e `session.max_rounds`); e o **pacote de aprovação não era domínio novo** (`title`/`summary`/`status`/`approval_threshold` chegam pelo MESMO `delegate.context` que já depositava `dialog_form_id` e `decisions`). Dois tipos escolhidos por MEDIÇÃO: `session.summary` é `texto` porque é lido **através da porta de masking** (`DialogFormRenderer.tsx:232`) e um tipo restritivo APAGARIA a tela de aprovação; `pergunta_coleta` é `texto` porque o valor é exibido **ao cliente**. ⚠️ Departura declarada: a nota de escopo mandava LISTAR campo que pedisse domínio novo e nomeava o copiloto — as 5 tags foram **declaradas**, porque já são escritas em `escopo.dominio.campo` pela própria plataforma e declará-las **não escolhe nome nenhum**; o que a nota protegia era *inventar*. Achado do próprio instrumento, da família *teste que não pode reprovar*: o censo publicou **80** aliases contra os **82** do oráculo porque o parser do mapa era **line-based** e descartava em silêncio a segunda linha de um `legado` quebrado — sub-contagem erra para o lado do trabalho a mais, o que a torna simpática e não menos falsa; quem a pegou foi **comparar o instrumento com o oráculo**, que é outra implementação
