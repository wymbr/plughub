# ADR: ContextStore como ALLOWLIST — tipo declarado, mapa de dados e vocabulário único de política

**Status:** Aceito — 2026-08-26. **Parcialmente implementado:** V0 *(metade)* · V1 · V1b · V2 · V2b
entregues; **V3 é a próxima**. V4 (a inversão, não reversível) segue **não iniciada**.
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

Medido nesta sessão: **12 `HSET` diretos no hash de contexto, em código de produção**, sem passar por
nenhum roteador de escopo, validação ou política:

| serviço | sites |
|---|---|
| `orchestrator-bridge/.../main.py` | :1322 · :1341 · :2145 · :4721 · :4752 · :8410 |
| `channel-gateway/.../adapters/webhook.py` | :562 · :1628 · :2169 · :2663 |
| `routing-engine/.../main.py` | :1175 · :1255 |

Mais os caminhos legítimos (SDK `ContextStore.set`, `context_set` do mcp-server, `context_tags` do
engine). **Qualquer política de escrita precisa decidir o que fazer com estes doze** — e a resposta
honesta não pode ser "presumir que passam pelo mesmo lugar", porque não passam.

### 1.8 Blast radius

`@ctx.` ou `:ctx:` aparecem em **486 ocorrências, 97 arquivos** (medido nesta sessão), dos quais ~40
são YAML de skill — isto é, **autoria**, não só código de plataforma. Este número é o que separa uma
mudança de chave de uma mudança de declaração.

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

### D7 — Fonte de verdade: config-api. O arquivo apenas semeia

Medido em 08-26: o `seed.py` e a config viva **divergiram nos dois sentidos**
(`session.vencimento_cartao` só no seed; `session.cpf_titular` só vivo), porque o seed foi editado
depois de a base estar semeada e seed-if-absent nunca reaplicou.

Decisão, alinhada à invariante de provisionamento do `CLAUDE.md`: **o config-api é a fonte de
verdade; o arquivo declarativo apenas semeia base vazia.** O que muda é que a divergência deixa de
ser invisível — o seed passa a **comparar e logar** o que difere em vez de pular mudo, e a tela mostra
a proveniência de cada nó (global × override de tenant).

---

## 3. As sete perguntas do briefing — respostas

**1. As regras vêm mesmo do config-api?** ✅ Sim, e a tela é que mentia — **fechado em 2026-08-26**.
A causa **não** era o `??` de `MaskingPage.tsx:160` (que nunca chegava a ser exercido): era colisão
de rota no proxy do platform-ui. Detalhe e lições no `CHANGELOG.md` § V0. Fica o método: a hipótese
herdada apontava um passo **adiante** do defeito, e só caiu porque foi medida antes de consertada.

**1b. Quem é a fonte de verdade entre seed e config viva?** → **D7**: config-api. O seed compara e
loga.

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
| **V3** | Mapa do ContextStore (D2) + aliases contados (D3) + **modo auditoria** (só registra o que teria sido escondido/recusado) | sim |
| **V4** | Inverter para deny-by-default, com a lista real que a V3 produziu | **não** |
| **V5** | Tela do pool vira seletor (D6); fechamento dos aliases cujo contador zerou | sim |

Ordem inegociável: **V1 antes de V4.**

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
- **V4** — a medição que autoriza virar a chave é *"o modo auditoria acusou zero campos não
  declarados por N dias"*, com o número de campos **declarados** ao lado. Zero sobre zero é um serviço
  parado, não uma allowlist completa.
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
