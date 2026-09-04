# ADR — Leitura de contexto com política por PLATEIA (um leitor, tipo declarado, plateia derivada)

**Status:** proposto
**Data:** 2026-09-04
**Componentes:** `packages/skill-flow-engine`, `packages/schemas`, `packages/mcp-server-plughub`,
`packages/orchestrator-bridge`, `packages/config-api`
**Relacionado:**
[`adr-contextstore-allowlist.md`](adr-contextstore-allowlist.md) *(criou o MAPA `tag → tipo` e o
catálogo `masking.types`; este ADR é o primeiro CONSUMIDOR de leitura das duas coisas)* ·
[`adr-masked-typed-declaration.md`](adr-masked-typed-declaration.md) *(o tipo decide exibição e
classe; aqui ele passa a decidir também o que cada PLATEIA recebe)* ·
[`adr-message-masking.md`](adr-message-masking.md) *(a trilha por DETECÇÃO em texto livre — eixo
distinto, ver §D8)* ·
[`adr-dialog-input-format-catalog.md`](adr-dialog-input-format-catalog.md) *(o mesmo catálogo, do
lado da ENTRADA)*
**Censo que o precede:** executado em 2026-09-04, números na §1. Falta portar para script
re-executável (F0).

---

## 0. Sumário da decisão

A plataforma **já declara** que um número de cartão não pode ecoar ao cliente — e ecoou. Não
faltou política: faltou quem a consultasse.

As três peças de *"pegar o dado já filtrado"* existem em duas casas e faltam numa:

> ⚠️ **Esta tabela foi CORRIGIDA em 2026-09-04, horas depois de escrita, e a correção
> muda o EIXO do ADR.** A versão original apontava `mascara.display.echo_to_*` como a
> peça de política e declarava o leitor inexistente. As duas linhas estavam erradas, e
> a segunda é a mais grave: **o leitor existe, é canônico, e um caminho já o usa** —
> foi ele que produziu o `***4444` da tela. Ver §1.1 e §D9.

| peça | onde | estado |
|---|---|---|
| tag → **tipo** | `masking.context_map` | ✅ 214 tags (com legado) |
| tipo → **máscara por plateia** | `masking.types.*.mascara.by_role` | ✅ 11 de 14 declaram `operator`; **`customer` em nenhum** |
| resolver plateia → máscara | `resolve_mask_for_audience` (`py-contextstore/masking.py:87`) | ✅ existe, e o docstring dele ANTECIPA este arco |
| aplicar a máscara ao valor | `apply_masking_type_to_value` / `applyMaskingTypeToValue` | ✅ com gate de paridade TS↔Python |
| derivar a plateia do **SÍTIO** | — | ❌ **é a peça que faltava**, e é o que este ADR acrescenta |
| **leitor** que junta as quatro | `interpolate` / `resolveCtxRef` | ❌ `HGET` cru — mas `_build_pending_preview` já faz o equivalente no outro caminho |

A decisão é fazer o leitor **um só** e ensiná-lo a perguntar duas coisas: *qual é o tipo?* (o mapa
responde) e *para quem vai?* (o **sítio** responde). Sem a segunda pergunta nenhum default serve —
cru é o de hoje, e mascarado quebra o `invoke` que manda o número ao CRM.

**Só a segunda pergunta é nova.** A primeira já tem resolvedor canônico e masker com paridade
medida; o que nunca existiu é derivar a plateia de um sítio (§D9). Reconhecer isso encolheu o
arco e evitou um terceiro vocabulário para *"quanto do valor aparece"*.

O que a medição acrescentou, e que muda o desenho: **quase nenhum dos acertos de um filtro por
TAG seria defeito.** Um censo por regex encontrou 20 pontos resolvendo para tipo com
`echo_to_customer: "none"`; o censo ESTRUTURAL — que sabe em que step cada interpolação está —
encontrou **2**. Os outros 18 não alcançam cliente nenhum: a maioria são argumentos de `invoke`
(`workflow_resume`), e quatro estão num skill sem `notify`/`menu`.

Uma regra por TAG reprovaria os 20 e quebraria o `workflow_resume`. É por isso que a plateia é
derivada do **sítio**, e não uma propriedade da tag.

Três coisas que este ADR deliberadamente **não** faz:

- **não decide o default de tag DESCONHECIDA** (§D4) — essa é a V4 do ADR da allowlist, tem
  população própria sendo contada e não pode ser virada de carona aqui;
- **não absorve a DETECÇÃO em texto livre** (§D8) — declarar e detectar respondem perguntas
  diferentes, e fundi-las devolveria duas respostas para *"que máscara este valor usa?"*;
- **não resolve `$.pipeline_state.*` na primeira fase** (§D7) — são 225 interpolações que o mapa
  não alcança, e o caminho existe mas é fatia própria.

---

## 1. Contexto — o que foi medido

Medição de 2026-09-04, ambiente demo, disparada por um teste do dono: o mesmo número de cartão
apareceu **`***4444`** numa tela e **`1111222233334444`** noutra.

### 1.1 Dois caminhos, um dado

```
skill_limite_entrada_v1:356   ← $.pipeline_state.pendencia.context.numero_cartao   →  ***4444
skill_limite_retorno_v1:71    ← @ctx.session.numero_cartao                         →  cru
```

> ⚠️ **CORRIGIDA em 2026-09-04, e é o TERCEIRO erro deste arco produzido por inferir em
> vez de medir.** Esta passagem dizia que o primeiro caminho *"lê um preview que alguém
> mascarou à mão"*. **É falso.** `_build_pending_preview`
> (`channel-gateway/adapters/webhook.py:2408-2472`) é allowlist, nomeia o **tipo** e
> resolve por `resolve_mask_for_audience(tipo, "customer")` + `apply_masking_type_to_value`
> — o leitor canônico, ciente de plateia, que este ADR estava propondo construir. O
> comentário dele (`:2424`) diz por que nomeia o tipo e não a máscara: para não virar
> *"o quarto motor de máscara do repositório"*.
>
> Os dois erros anteriores foram do mesmo feitio (§1.3, e o eixo em §D9): **afirmar a
> partir do tipo de coisa em vez de ler o código que a produz.**

O primeiro lê um preview produzido pelo **leitor canônico ciente de plateia**; o segundo lê o
ContextStore direto. A proteção existe num e não no outro porque o segundo caminho nunca foi
ligado a ele — não porque alguém a tenha escrito à mão.

**Isso reduz o arco e muda o alvo.** O que a tela 3 precisa não é política nova:
`resolve_mask_for_audience(credit_card, "customer")` devolve `last_4` **hoje** (pelo fallback ao
`operator`, o único eixo declarado), e `apply_masking_type_to_value("1111222233334444", "last_4")`
= `***4444` — exatamente o que a tela 2 mostra. Falta o segundo caminho perguntar.

Varredura do Redis no mesmo dia: o valor **cru** está em **2 streams canônicos** e **3 hashes de
ctx** (sessão + journey).

### 1.2 A política já existia, e diz o contrário do que aconteceu

```
context_map:  contexto.session.cartao.numero → credit_card
              legado: ["session.numero_cartao"]        ← a tag que o flow LÊ
masking.types.credit_card.mascara:
              by_role: { operator: "last_4" }        ← o eixo de EXIBIÇÃO (este ADR)
              display.echo_to_customer: "none"       ← outro eixo: eco da ENTRADA mascarada
              display.echo_to_operator: "masked"
```

A tag que o flow interpola **é alias declarado** da canônica tipada. Nada disso é consultado na
leitura.

⚠️ **Os dois campos respondem perguntas diferentes, e este ADR leu o errado por algumas horas.**
`echo_to_*` trata do valor que o cliente **digitou** voltando no evento de interação, e o próprio
schema o declara ADVISORY (`audit.ts:342`: *"o cliente digitou o valor, já o conhece"*) — é medida
contra quem olha por cima do ombro, não contra o titular do dado. A pergunta deste arco é *quanto
de um valor ARMAZENADO a plataforma mostra ao renderizá-lo*, e quem a responde é `by_role`. Ver
§D9.

⚠️ As regras `*.numero_cartao → last_4` de `masking.context_rules` existem e estão vivas — mas com
`role: operator`. Elas protegem **quem lê o ctx**, não a interpolação num texto. É outro eixo, e
confundi-los faria parecer que já havia cobertura.

### 1.3 Os acertos do filtro NÃO são todos defeitos

> ⚠️ **Esta seção foi CORRIGIDA em 2026-09-04, no mesmo dia, pelo censo estrutural (CTX-01).** A
> versão anterior dizia que *"10 dos 20 são `credential` num link que o cliente precisa receber"*.
> Isso era **inferência a partir do TIPO**, não medição do sítio — e o parse estrutural a refutou:
> aqueles `credential` estão em steps `invoke` (`tool: workflow_resume`, `input.resume_token`),
> plateia `system`. Não chegam a cliente nenhum. A conclusão do ADR sobrevive e sai mais forte; o
> que estava errado era a evidência que a sustentava. *É o próprio erro que o ADR nomeia — medir a
> tag em vez do sítio — cometido ao escrever o ADR.*

**Censo por REGEX** (o primeiro, e o que mede a proposição errada):

| população | n |
|---|---|
| interpolações `@ctx.*` nas skills | **167** |
| interpolações `$.pipeline_state.*` | **225** |
| `@ctx.*` que resolvem para tipo com `echo_to_customer: "none"` | **20** |

**Censo ESTRUTURAL** (`q_ctx_read_audience_census.ts` — sabe em que step cada uma está):

| grandeza | n |
|---|---|
| interpolações `@ctx.*` em campo que vira texto ou argumento | **88** |
| que alcançam o **cliente** | **7** |
| operador · sistema · modelo | 3 · 72 · 6 |
| **que MUDARIAM** (alguma máscara se aplicaria) | **3** |
| tags fora do mapa (contadas, não decididas — §D4) | 6 nomes |

> ⚠️ **Eram 2 sob o eixo `echo_to_*`; são 3 sob `by_role`, e o terceiro é o achado que só o
> eixo certo produz.** Ver §D9.1.

Duas são `skill_limite_retorno_v1` / `notificar_aprovado` e `notificar_recusado`, ambas
`session.numero_cartao` → `credit_card` → **`last_4`**. **São exatamente as duas que a tela do dono
exibiu**, e a máscara que sairia é a mesma `***4444` da tela 2.

A terceira é `notificar_aprovado` / `session.limite_aprovado` → `financial` → **`financial`**
(`R$ ****,**`), e ela **não é defeito: é o fallback errando** (§D9.1).

**É esta diferença — 20 contra 2 — que decide o desenho.** Um filtro por TAG reprovaria os 20, e o
modo de falha seria o `workflow_resume` parando de receber o token: *"o processo travou"*, que
ninguém liga a uma política de mascaramento.

**Consequência de método, e ela vale mais que o número:** a regex responde *"a tag APARECE no
arquivo?"*; a pergunta era *"a tag CHEGA a alguém?"*. As duas se parecem o bastante para uma passar
por outra, e a diferença aqui foi de uma ordem de grandeza.

---

## 2. Decisões

### D1 — UM leitor, e ele SUBSTITUI; nunca uma segunda porta

O ponto é o `interpolate` do engine: o funil por onde todo `@ctx.*` e `$.pipeline_state.*` vira
texto antes de ir para um `notify`, um `menu`, um argumento de `invoke` ou um prompt de `reason`.

**Não** se acrescenta um `getMasked()` ao lado do `get()`. Duas portas para o mesmo dado e só uma
trancada é o achado que o `CLAUDE.md` já registra sobre `/sessions/{id}/stream` — e ali a porta
destrancada era justamente a que servia a transcrição inteira. Quem escreve template escolheria a
que funciona, que é sempre a permissiva.

### D2 — a PLATEIA é derivada do SÍTIO, não declarada por uso

Declarar a plateia em cada interpolação são 392 declarações e 392 chances de esquecer. O sítio já
carrega a informação:

| sítio | plateia | política |
|---|---|---|
| `notify` / `menu`, `visibility: "all"` | cliente | `by_role.customer` (hoje: fallback ao `operator`) |
| `notify` / `menu`, `agents_only` ou array de participantes | operador | `by_role.operator` |
| `invoke` — argumento de tool | sistema | **cru**. ⚠️ *"e o gate é o que já existe"* era FALSO — ver §D10 |
| `reason` — prompt de LLM | ver **D5** | decisão própria |

`visibility` já é resolvido antes do envio, em todo `notify` e `menu`. A derivação não pede campo
novo em lugar nenhum — só que alguém a leia.

**Corolário que a medição sustenta:** a mesma tag num skill de *workflow* e num skill de *agente*
tem plateias diferentes, e é por isso que a regra não pode morar na tag. Os 4 pontos de
`skill_limite_processo_v1` não são defeito **porque aquele skill não tem como falar com o cliente**.

### D3 — default restritivo; exceção DECLARADA, greppável e auditada

Quando o tipo diz `none` para a plateia derivada, o valor **não** sai. A exceção — o token dentro do
link — é declarada **no sítio**, com marca explícita no template.

Três exigências sobre a exceção, e nenhuma é decorativa:

1. **greppável** — quem audita precisa achar as 10 sem ler 44 YAMLs;
2. **auditada** — a exceção entra no registro, porque *"o valor saiu em claro"* é fato de
   conformidade, não detalhe de rendering;
3. **contada** — se o número crescer, a política virou carimbo.

Precedente: `resolve_scope`, onde o restritivo vence **porque o permissivo degrada mudo**.

### D4 — este ADR NÃO decide o default da tag desconhecida

Tag fora do mapa continua com a semântica que o
[`adr-contextstore-allowlist`](adr-contextstore-allowlist.md) lhe dá hoje, e a inversão é a **V4
daquele ADR** — que é irreversível, tem população própria sendo contada, e não pode ser virada de
carona por um ADR de leitura.

O que este acrescenta é **um contador a mais na mesma conta**: quantas interpolações resolvem para
tag não declarada. Se o número for alto, é evidência para a V4; não é autorização para ela.

### D5 — `reason` é uma plateia PRÓPRIA, e não "operador"

Um prompt de LLM sai da plataforma. Mandar um CPF para o provedor de modelo é um fato diferente de
mostrá-lo a um operador logado — outra fronteira, outro contrato, outra classe LGPD em jogo.

Dobrar `reason` em `by_role.operator` seria escolher a política mais frouxa por conveniência de
tabela. Fica com decisão própria e **não entra na primeira fase**: hoje é o caminho mais permissivo
por omissão, e trocar omissão por decisão errada não é progresso.

> **Medido na F4 (2026-09-04): a população é ZERO, e é isso que sustenta o adiamento.**
> Seis interpolações `@ctx.*` chegam a prompt, em cinco tags distintas — três são `texto`
> (`by_role` vazio, aberto por declaração) e duas não estão no mapa
> (`session.historico_mensagens`, `session.historico_resumo`, que caem no §D4). **Nenhuma
> carrega tipo que mascare para alguém.**
>
> Decidir a D5 agora seria política contra população zero, que é o erro que este
> repositório já registrou. O que protege o prompt hoje não é uma regra — é o fato de
> ninguém estar mandando PII para lá, e **esse fato virou gate** (ramo G): ele reprova no
> instante em que uma tag de tipo que mascara aparecer num `reason`. É o gatilho da fase,
> não a fase.

### D6 — a política é do TIPO, e o sítio só escolhe a coluna

O sítio responde *para quem*; **quem decide o quê** é sempre o tipo, no catálogo. Um template não
pode afrouxar — só pode pedir exceção (D3), que é visível.

Isso mantém a propriedade que o ADR do `masked` tipado comprou: existe **uma** política de CPF no
tenant, e ela mora num lugar que compliance edita.

### D7 — `$.pipeline_state.*` é FASE 2, com o caminho nomeado

São **225** interpolações, e o mapa não as alcança: ele tipa tag de ContextStore, não chave de
`pipeline_state`. A tela que mostrou `***4444` lê justamente daí.

O caminho existe e é **proveniência**: o `menu` já envia `masked_types` ao bridge *"para REGISTRAR a
proveniência"* (T3 do ADR do `masked` tipado). Carimbar o tipo ao gravar a resposta em
`pipeline_state` daria ao interpolador a mesma pergunta respondida.

Fica fora da fase 1 porque é maior e tem decisão própria (o que fazer com valor derivado — uma
concatenação de dois campos herda o tipo de qual?).

### D11 — ~~a F5 está bloqueada~~ · **REBAIXADA pela §D12 (2026-09-04)**

> ⚠️ **O bloqueio caiu no mesmo dia, e por medição.** Esta seção concluía que sem carimbo
> de proveniência não havia aplicação segura, porque `pendencia.context.*` já nasce
> mascarado e re-mascarar corromperia. **A premissa que faltava: a rede de detecção é
> IDEMPOTENTE** — o valor já mascarado não casa padrão nenhum (medido nos quatro tipos).
> Logo aplicar sobre ele é no-op, e o carimbo deixa de ser pré-requisito de segurança.
>
> O carimbo continua **desejável**, por precisão: a rede pega 4 de 15 tipos e só por
> forma, enquanto o carimbo daria o tipo declarado. Vira dívida (CTX-10), não bloqueio.
>
> O resto desta seção continua valendo como a MEDIÇÃO que a produziu.

*(Medido em 2026-09-04.)*

**O número muda, como na CTX-01.** O ADR cita 225, e veio de regex. O censo estrutural — a
mesma derivação de plateia que o produto usa:

| grandeza | n |
|---|---|
| `$.pipeline_state.*` por regex | **228** |
| em campo que vira texto ou argumento | **142** |
| que alcançam o **cliente** | **35** (30 chaves distintas) |
| operador · sistema · modelo | 3 · 74 · 30 |

E das 35, a maioria não é dado do titular: 11 são `roteiro.render.by_node.*` e 5
`dialog.render.*` — texto de DialogForm já renderizado. O que sobra de PII é um punhado.

**O bloqueio não é tamanho: é que aplicar hoje CORROMPERIA valor.** `pendencia.context.*`
**já nasce mascarado** — é o `_build_pending_preview` que o escreve, e o próprio skill
documenta (`skill_limite_entrada_v1.yaml:346`): *"o cartão vem ***1234"*, com o aviso de que
envolvê-lo produziria `*****4444**`.

Olhando só o valor, `***4444` e `1111222233334444` são indistinguíveis quanto a *"isto já
passou por uma máscara?"*. Logo:

> **O carimbo de proveniência deixou de ser refino e passou a ser requisito de correção.**
> Sem ele não existe aplicação segura — só aplicação que às vezes mascara duas vezes.

⚠️ E a tentação óbvia está **proibida pela §D8**: detectar se o valor "parece mascarado"
(procurar `***`) seria construir um detector onde o arco exige declaração, e a V2b do ADR da
allowlist fechou exatamente esse defeito.

**A decisão que falta é do dono**, e ela é a que o §D7 já anunciava: um valor DERIVADO
(concatenação de dois campos, um resumo gerado por LLM) herda o tipo de qual? Enquanto ela
não for tomada, o carimbo não tem regra para os casos compostos — e são eles a maior parte
de `roteiro.render.*`. Registrado como **CTX-10**.

### D12 — a ordem é DECLARAR > aplicar > rede, e a rede é MITIGAÇÃO, nunca controle

*(Decisão do dono, 2026-09-04. Ela reordena a §D8 — ver o fim desta seção.)*

**A regra de produto vem primeiro, e é ela que carrega o peso:**

> Sempre que possível, **nunca capturar dado em texto livre** — usar `DialogForm`, que
> declara o campo e o tipo. O LLM fica no **orquestrador**; os agentes especialistas
> devem ser `DialogForm` sempre que der.

Isso não é preferência de estilo: é a única camada que dá **garantia**. Um campo
declarado tem tipo, e tipo é fato. Tudo abaixo disso é estimativa.

| camada | o que dá | alcance |
|---|---|---|
| **1. declarar** (`DialogForm`) | garantia | o que o roteiro coleta |
| **2. aplicar na leitura** (F3) | garantia, para o que foi declarado | tag no mapa |
| **3. rede de detecção** | **mitigação** | o que é reconhecível por FORMA |

⚠️ **A camada 3 não é cobertura, e chamá-la assim seria o pior desfecho deste arco.**
Uma rede apresentada como controle faz alguém relaxar sobre capturar em texto livre — é
o "valor plausível" na forma mais cara: um anestésico que remove o incômodo que levaria
a declarar o campo. Ela existe para reduzir dano onde a captura já aconteceu, nunca para
autorizá-la.

**Os limites, medidos em 2026-09-04:**

- **4 de 15 tipos** têm `detect_pattern` — `cpf`, `credit_card`, `phone`, `email_addr`.
  Os 11 restantes não são detectáveis, e vários **por decisão** (o `card_expiry` não tem
  padrão porque `\d{2}/\d{2}` casaria qualquer data).
- Ela pega o que é reconhecível por **forma**; nada do que é sensível por **contexto**.
- Nem com LLM o acerto é 100% — e por isso o LLM não transforma a camada 3 em garantia,
  só melhora uma estimativa.
- **"Existe a função" ≠ "ela roda aqui"**: o MSK-02 é a prova — o cartão chegou CRU ao
  stream canônico apesar de o padrão casar 16 dígitos.

**O que a rede tem de bom, e foi o que destravou a F5:** ela é **idempotente**. Medido
nos quatro tipos detectáveis — o valor cru casa, o já mascarado **não**:

| tipo | cru | já mascarado |
|---|---|---|
| `credit_card` | casa | `***4444` não casa |
| `cpf` | casa | `***.***.***.--` não casa |
| `phone` | casa | `(##) ****-4321` não casa |
| `email_addr` | casa | `m***@exemplo.com` não casa |

É isso que dissolve o bloqueio da §D11: aplicar a rede sobre `pendencia.context.*`, que
**já nasce mascarado**, é no-op. **O carimbo de proveniência deixa de ser pré-requisito
de segurança** — vira otimização de precisão, e a §D11 é rebaixada de bloqueio a dívida.

⚠️ **Correção de premissa que o dono levantou e que vale registrar:** *"quando o tipo
existe, o valor já está gravado no formato adequado"* vale para **FORMATO**
(`dd/mm/aaaa`, moldado na coleta pelo catálogo de formatos) e **não** para **MÁSCARA**. O
`adr-masked-typed-declaration` decide que o tipo governa exibição e classe, **nunca
persistência** — medido aqui: `session.numero_cartao` está cru no ctx e em dois streams
canônicos (MSK-02). O valor tipado é persistido inteiro **de propósito** (o `invoke` do
CRM precisa dele), e é por isso que a máscara é aplicada na LEITURA.

**Onde o LLM entra, se entrar:** fora do caminho síncrono. As duas implementações da rede
são declaradamente *"Pure/synchronous — no vault, no I/O"*, e são chamadas por
interpolação, dentro do turno. Um LLM ali muda o contrato de todos os chamadores — custo
e latência por interpolação, num caminho que hoje é O(regex). O precedente é o
sentimento: chamada dedicada, fora do turno.

### D12.1 — isto NÃO reabre o defeito que a §D8 fechou

A §D8 diz que este arco *"não absorve a DETECÇÃO"*, e a V2b do ADR da allowlist fechou o
defeito de tê-las fundidas. A distinção que sustenta a §D12:

- **o que a V2b proibiu** foi um campo do catálogo respondendo às DUAS perguntas — *"que
  máscara este tipo usa?"* e *"como reconheço este dado num texto?"* —, porque aí uma
  resposta contamina a outra;
- **o que a §D12 faz** é usá-las como dois mecanismos SEPARADOS, em momentos diferentes,
  com status diferentes: declaração dá garantia, rede dá mitigação, e a hierarquia entre
  as duas é explícita.

E há um fato novo que a §D8 não tinha: **a idempotência foi medida depois dela.** Quando
escrevi *"não absorve a detecção"* eu não sabia que a rede podia rodar sobre valor já
mascarado sem estragá-lo — e era esse o risco que justificava mantê-la fora.

### D8 — não absorve a DETECÇÃO

`detect_pattern` acha PII em texto livre; este leitor aplica política a um valor **declarado**. São
perguntas diferentes em momentos diferentes, e a V2b do ADR da allowlist fechou exatamente o defeito
de tê-las fundidas.

⚠️ **REORDENADA pela §D12 (2026-09-04).** O que segue continua valendo para a camada de
DECLARAÇÃO — detectar e declarar seguem sendo perguntas diferentes, e fundi-las num campo
só continua proibido. O que mudou é que a detecção passou a ter lugar **abaixo** da
declaração, como mitigação nomeada, em vez de ficar inteiramente fora. Ver §D12.1.

⚠️ Registro de uma pergunta que este ADR **não** responde e que a medição levantou: o
`detect_pattern` de `credit_card` casa 16 dígitos sem separador, e mesmo assim a mensagem de saída
foi persistida crua no stream. **Por que a detecção não mascarou aquela mensagem** é assunto do
`adr-message-masking`, e está registrado como MSK-02.

### D9 — o eixo é `by_role`, e o resolvedor é o que JÁ EXISTE

*(Decisão tomada em 2026-09-04, algumas horas depois da primeira versão deste ADR, ao medir para
começar a F3. Ela **substitui** o uso de `mascara.display.echo_to_*` que a versão original fazia.)*

São dois campos, e eles respondem perguntas diferentes:

| campo | pergunta | força |
|---|---|---|
| `mascara.display.echo_to_*` | o valor que o cliente **digitou** volta no evento de interação? | ADVISORY para o cliente, por declaração do schema (`audit.ts:342`) |
| `mascara.by_role` | quanto de um valor **armazenado** aparece para um papel? | é o eixo que o masker consome |

A pergunta deste arco é a segunda. Três consequências:

1. **`by_role` é estritamente mais expressivo** — diz QUAL máscara (`last_4`, `email_domain`,
   `hidden`), e não apenas que há alguma. Para *renderizar* `masked` a `by_role` seria necessária
   de qualquer forma; construir sobre `echo_to_*` teria exigido consultá-la logo depois.
2. **O resolvedor não precisa ser escrito** — `resolve_mask_for_audience`
   (`py-contextstore/masking.py:87`) tem os três ramos e o docstring dele **antecipa este arco**:
   *"quando o eixo `customer` existir, o segundo ramo o pega sem mudar nada aqui"*.
3. **Já há consumidor com a plateia `customer` viva** — `_build_pending_preview` (§1.1).

Insistir no `echo_to_*` teria criado um **terceiro** vocabulário para *"quanto do valor aparece"*,
ao lado de `by_role` (9 valores) e `TokenDisplayMode` (3) — que é exatamente o que o comentário
daquele consumidor existe para impedir.

**O que este ADR de fato acrescenta**, depois da correção: `deriveAudience` — o resolvedor canônico
recebe a audiência PRONTA e nunca soube derivá-la de um sítio. E o gêmeo TS do resolvedor, que não
existia.

### D9.1 — o `operator` não é fallback: é o TETO do que um agente pode mostrar

> ⚠️ **Esta seção foi REESCRITA em 2026-09-04, e a versão anterior está refutada.** Ela
> dizia que o fallback ao `operator` *"é evidência boa e aplicador ruim"*, e que o remédio
> era declarar `by_role.customer` por tipo. Errado nos dois pontos, pela razão do dono:
> **quem compõe um `notify` é um AGENTE**, e um agente — IA ou humano — só pode mostrar ao
> cliente o que ele próprio vê. A visão dele é a do `operator`.
>
> Logo o segundo ramo de `resolve_mask_for_audience` não é *"a única audiência declarada,
> então serve"*: é o **teto correto por construção**, com razão nomeada. E `by_role.customer`
> **nunca precisa existir** — o que remove a segunda declaração da mesma intenção que a
> versão anterior estava criando.

Consequências, e a terceira é a que fecha o desenho:

1. **A F2 perde o conteúdo.** Não há exceção a declarar nem eixo novo a preencher. A
   ordenação *"a exceção vem ANTES da aplicação"* foi escrita supondo exceções; a população
   é zero e o mecanismo passou a ser desnecessário.
2. **O que sobra de residual é estritamente menor que hoje.** Um template que ponha valor
   interno num `notify` de cliente vaza no nível do operador, não cru — e hoje vaza cru.
   Fechar mais exigiria a declaração que este ADR deixou de pedir.
3. **Quando o resultado parece errado, o que se inspeciona é a DECLARAÇÃO** — nunca o
   leitor. Foi o que aconteceu no primeiro caso medido, abaixo.

### D9.2 — "mostrável ao cliente" é uma FINALIDADE, e precisa de tipo próprio

O censo estrutural achou `notificar_aprovado` / `session.limite_aprovado` → tipo `financial`
→ máscara `financial` (`R$ ****,**`) — sobre o limite que a mensagem existe para anunciar.

Sob a §D9.1 isso **não é o leitor mascarando demais**: é propagação fiel de uma declaração.
O que estava errado era a declaração, e a razão é geral — *consulta de saldo que mascara o
saldo não é produto*. Existe uma classe de valor monetário cujo processo existe para
comunicá-lo, e ela precisava de tipo.

O catálogo já tinha metade do eixo. `valor_declarado_pelo_cliente` (2026-09-02) resolveu o
`limite_solicitado` pelo critério da **assimetria**: *"um valor que o próprio titular
declarou não é dado a proteger DELE"*. E excluía explicitamente o outro lado:

> ⚠️ **NÃO usar para valor que a EMPRESA decidiu (limite aprovado, saldo, fatura)** — aqueles
> o cliente não declarou, e a assimetria é o critério.

**A exclusão não estava errada; estava incompleta.** A assimetria responde *"é dado do
titular?"*, e não responde *"o processo existe para comunicar isto?"*. São duas perguntas, e
só a primeira tinha resposta. Por que a lacuna passou: a decisão foi tomada contra o caminho
do **preview**, cuja spec declara `status`, `numero_cartao` e `limite_solicitado` —
`limite_aprovado` **não aparece lá**. Quem o mostra é o `notify`, e ali nada aplicava política
ainda. **A F3 é o que faz esse caminho passar a aplicar.**

Nasce então a segunda ponta, mesmo molde, máscara vazia e classe LGPD preservada:

| tipo | finalidade | `by_role` | `lgpd` |
|---|---|---|---|
| `valor_declarado_pelo_cliente` | o cliente pediu | `{}` | `financeiro` |
| **`valor_informado_ao_cliente`** | a empresa respondeu — limite, saldo, extrato | `{}` | `financeiro` |

⚠️ **O nome é a FINALIDADE, não o formato**, e a advertência é do próprio catálogo: `moeda`,
`valor_aberto` ou `numero_plain` seriam *"arma carregada apontada para o próximo campo
financeiro"* — todo valor monetário futuro herdaria a permissão por **parecer** dinheiro. Aqui
a permissão vem de a empresa ter decidido informar **aquele** valor **àquele** titular.

Efeito medido no censo: **3 → 2**. Sobram as duas interpolações de `session.numero_cartao`,
ambas `→ last_4`, que é o `***4444` que a outra tela já mostra.

### D10 — o `invoke` sai CRU e **não é gateado**; a §D2 afirmava o contrário

*(Medido na F4, 2026-09-04. Corrige uma afirmação da própria §D2.)*

A §D2 dizia que o valor cru num argumento de tool era aceitável porque *"o gate é o que já
existe (`AuditPolicy.data_categories`)"*. **Esse gate não está em vigor.** Medido no
repositório inteiro:

| fato | medição |
|---|---|
| `data_categories` declarado no schema | ✅ `audit.ts:35` |
| tools que o declaram | **0** |
| quem o LÊ | `sdk/src/mcp-interceptor.ts` — e o `CLAUDE.md` mede que ele **nunca é instanciado** |
| ocorrências em `mcp-server-plughub/src` | **0** |

Ou seja: o caminho `invoke` manda o valor inteiro, e **nenhuma das duas metades da frase
se sustenta como proteção** — é intencional (o CRM precisa do número), e não é gateado.

**A decisão NÃO muda:** mascarar argumento de tool quebraria o produto, e o `invoke` continua
cru. O que muda é parar de citar um gate inexistente como se fosse a razão — *comentário que
promete invariante sem mecanismo* é a família que o `CLAUDE.md` caça, e aqui ela estava dentro
de um ADR de conformidade.

O buraco em si é maior que este arco (é a borda MCP, e tem ADR próprio:
[`adr-mcp-interception-single-border`](adr-mcp-interception-single-border.md)). Fica
registrado como **CTX-09**.

---

## 3. Alternativas refutadas

| alternativa | por que não |
|---|---|
| **Filtrar por TAG** (a tag sensível nunca sai) | Reprovaria 20 pontos onde o censo estrutural acha **2** (§1.3). Os 18 restantes são majoritariamente argumento de `invoke`, então o sintoma seria o `workflow_resume` parando — *"o processo travou"*, que ninguém liga a mascaramento |
| **Segunda porta (`getMasked()` ao lado do `get()`)** | Duas portas, e a destrancada é a que vale. É o achado do `/sessions/{id}/stream`, verbatim |
| **Declarar a plateia em cada interpolação** | 392 declarações e 392 chances de esquecer; e a que faltar degrada para o permissivo |
| **Filtrar no adapter de canal** | Tarde demais: quando o texto chega lá, o valor **já está** no `pipeline_state` e no stream canônico — medido, 2 streams com o número cru. Filtrar na borda protege a tela e não o registro |
| **Mascarar tudo por default, sem plateia** | Quebra `invoke`: o CRM precisa do número inteiro. Um default único está errado para metade dos sítios |
| **Usar `mascara.display.echo_to_*` como eixo** (a 1ª versão deste ADR) | Responde outra pergunta — eco da ENTRADA mascarada, e ADVISORY para o cliente por declaração do schema. Seria um terceiro vocabulário para *"quanto do valor aparece"*, e ainda precisaria de `by_role` para renderizar `masked` (§D9) |
| **Aplicar o fallback ao `operator` também ao cliente** | Mesmo fallback, um acerto (`credit_card`) e um erro (`financial`, que apagaria o limite anunciado). É evidência, não decisão (§D9.1) |

---

## 4. Fases

**A ordem tem uma inversão deliberada: a EXCEÇÃO vem antes da APLICAÇÃO.** Aplicar primeiro
quebraria os 10 usos legítimos, e um arco que quebra o produto na primeira fase não chega à segunda.

| fase | entrega | por que aqui |
|---|---|---|
| **F0** | censo re-executável: interpolações por sítio × tipo × plateia derivada, com os 20 **classificados** (legítimo / defeito / sem plateia de cliente) | O número sozinho não diz de qual proposição é evidência (§1.3) |
| **F1** | o resolvedor de plateia + **modo auditoria**: calcula o que faria e LOGA, sem aplicar | Mesmo desenho da V3 da allowlist. É o que transforma "acho que são 10" em contagem |
| **F2** | ~~exceção declarada~~ — **sem conteúdo** (D9.1/D9.2). O que a fase exigia virou tipagem por FINALIDADE, feita em 2026-09-04: `valor_informado_ao_cliente` criado e 3 tags retipadas | A população de exceção é zero e o mecanismo é desnecessário. O que restava (o limite mascarado) era declaração errada, não falta de exceção |
| **F3** ✅ | aplicar em `notify`/`menu` — o caminho de cliente. **Entregue em 2026-09-04**: `filtrarLeituraCtx` SUBSTITUI o valor no `interpolate`, e `auditarLeituraCtx` foi REMOVIDA (duas funções calculando a mesma regra é o defeito deste arco) | Onde estão os defeitos medidos |
| **F4** ✅ | `invoke` e `reason` — **MEDIDA, não construída** (2026-09-04). O `invoke` segue cru e o gate que a §D2 citava **não existe** (§D10). O `reason` tem população **ZERO**, então decidir a §D5 agora seria política contra zero: o fato virou **gate** (ramo G), que é o gatilho da fase | Confirmar era o trabalho, e confirmar produziu duas correções |
| **F5** ✅ | `$.pipeline_state.*` — entregue em 2026-09-04 pela **REDE** (§D12), não pelo carimbo. Censo: 228 por regex → **142** estruturais → **35** ao cliente, 30 chaves | A idempotência da rede dispensou o carimbo, que virou dívida de PRECISÃO em vez de bloqueio |

---

## 5. Gates

| gate | prova | testemunha negativa |
|---|---|---|
| `probe_ctx_read_audience_census.sh` | o censo da F0 reproduz os números, classificados | zero interpolações ⇒ INCONCLUSIVO, nunca verde |
| `probe_ctx_read_audience_resolve.sh` | a plateia derivada bate com o sítio nos quatro casos da D2 | sítio `agents_only` **não** pode derivar `cliente` |
| `probe_ctx_read_policy_applied.sh` | `credit_card` para plateia cliente não sai cru | o **mesmo** valor para plateia sistema (`invoke`) sai INTEIRO — senão o gate passa por bloquear tudo |
| paridade do gêmeo (§D9) | `resolveMaskForAudience` (TS) e `resolve_mask_for_audience` (Python) concordam nos três ramos | um ramo divergente reprova — divergir aqui reabre as duas respostas que o `_build_pending_preview` existe para não criar |
| `probe_ctx_read_exception_visible.sh` | a exceção declarada aparece no censo e no registro | exceção não declarada ⇒ valor bloqueado, e o gate prova os dois lados |

O terceiro é o que carrega o arco: **um filtro que bloqueia tudo passa em qualquer teste que só
verifique bloqueio.**

---

## 6. Dívida que este ADR NÃO fecha

- **MSK-02** — por que a detecção não mascarou a mensagem de saída, dado que o padrão casa (§D8).
- **A V4 da allowlist** — o default da tag desconhecida (§D4).
- **Valor derivado em `pipeline_state`** — concatenação de dois campos herda o tipo de qual (§D7).
- **O `display` copiado de moeda** em `address`/`health`/`financial` de `masking.types` (FMT-07):
  aqui ele passa a ser LIDO, então deixa de ser cosmético.
- **DUAS casas ligam valor → tipo, e elas já discordaram** (CTX-08): `masking.context_map`
  (que a F3 vai usar) e a spec `preview` do `delegate`, declarada por call site
  (`skill_limite_processo_v1.yaml:114`). Sobre `limite_solicitado` diziam `financial` e
  `valor_declarado_pelo_cliente` — mascarado num caminho, aberto no outro. Alinhadas à mão em
  2026-09-04; nada impede a próxima divergência.
- **O guard de escopo cobre UMA chave de um par com o mesmo modo de falha** (CNS-17):
  `masking.context_map` recusa override de tenant porque a resolução é tenant-vence-global
  POR INTEIRO; `masking.types` é a chave irmã, tem o mesmo modo de falha e **não tem o
  guard**. Medido ao errar: um `PUT` com `tenant_id` criou o override e congelou o catálogo
  do tenant em 15 tipos, desligado do global.
- ~~**O masker não é alcançável a partir do engine** (CTX-07)~~ — **fechado em 2026-09-04.** O
  corpo de `applyMaskingTypeToValue` mudou de casa para `@plughub/schemas` e o `mcp-server`
  reexporta; o gate de paridade (30 vetores) segue verde e passou a medir a casa canônica.
  Provado do engine: `credit_card` ao cliente sai `***4444`, ao sistema sai cru. **A F3 deixa de
  ter bloqueio técnico.**
