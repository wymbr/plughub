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

| peça | onde | estado |
|---|---|---|
| tag → **tipo** | `masking.context_map` | ✅ 214 tags (com legado) |
| tipo → **política por plateia** | `masking.types.*.mascara.display` | ✅ `echo_to_customer` / `echo_to_operator` |
| **leitor** que aplica | `interpolate` / `resolveCtxRef` | ❌ `HGET` cru, não consulta nenhuma |

A decisão é fazer o leitor **um só** e ensiná-lo a perguntar duas coisas: *qual é o tipo?* (o mapa
responde) e *para quem vai?* (o **sítio** responde). Sem a segunda pergunta nenhum default serve —
cru é o de hoje, e mascarado quebra o `invoke` que manda o número ao CRM.

O que a medição acrescentou, e que muda o desenho: **os acertos do filtro não são todos defeitos.**
Dos 20 pontos que resolvem para tipo com `echo_to_customer: "none"`, metade é `credential` num link
que o cliente **precisa** receber. Uma regra por TAG reprovaria os 20 e quebraria o survey. É por
isso que a plateia é derivada do **sítio**, e não uma propriedade da tag.

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

O primeiro lê um preview que **alguém mascarou à mão**; o segundo lê o ContextStore direto. A
proteção existe num e não no outro porque é decisão de cada template — que é a definição de
*"política por formulário"*, o defeito que o ADR do `masked` tipado nomeia.

Varredura do Redis no mesmo dia: o valor **cru** está em **2 streams canônicos** e **3 hashes de
ctx** (sessão + journey).

### 1.2 A política já existia, e diz o contrário do que aconteceu

```
context_map:  contexto.session.cartao.numero → credit_card
              legado: ["session.numero_cartao"]        ← a tag que o flow LÊ
masking.types.credit_card.mascara.display:
              echo_to_customer: "none"
              echo_to_operator: "masked"
              by_role: { operator: "last_4" }
```

A tag que o flow interpola **é alias declarado** da canônica tipada. O tipo **proíbe** eco ao
cliente. Nada disso é consultado na leitura.

⚠️ As regras `*.numero_cartao → last_4` de `masking.context_rules` existem e estão vivas — mas com
`role: operator`. Elas protegem **quem lê o ctx**, não a interpolação num texto. É outro eixo, e
confundi-los faria parecer que já havia cobertura.

### 1.3 Os acertos do filtro NÃO são todos defeitos

| população | n |
|---|---|
| interpolações `@ctx.*` nas skills | **167** |
| interpolações `$.pipeline_state.*` | **225** |
| `@ctx.*` que resolvem para tipo com `echo_to_customer: "none"` | **20** |

Dos 20:

- **10 são `credential`** (`resume_token` / `delegate_resume_token`) — e um token dentro de um link
  de survey ou OTP vai ao cliente **por desenho**;
- **4 estão em `skill_limite_processo_v1`**, que tem **zero** steps `notify`/`menu`: é workflow, o
  perfil o proíbe de falar com o cliente. Não há eco a filtrar;
- **os restantes**, entre eles os dois de `skill_limite_retorno_v1` (`credit_card`), são os defeitos.

**É esta linha que decide o desenho.** Um filtro que julgasse pela TAG reprovaria os 20 — e o modo
de falha seria *"o cliente recebeu a mensagem sem o link"*, que ninguém liga a uma política de
mascaramento.

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
| `notify` / `menu`, `visibility: "all"` | cliente | `echo_to_customer` |
| `notify` / `menu`, `agents_only` ou array de participantes | operador | `echo_to_operator` |
| `invoke` — argumento de tool | sistema | **cru**, e o gate é o que já existe (`AuditPolicy.data_categories`) |
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

Dobrar `reason` em `echo_to_operator` seria escolher a política mais frouxa por conveniência de
tabela. Fica com decisão própria e **não entra na primeira fase**: hoje é o caminho mais permissivo
por omissão, e trocar omissão por decisão errada não é progresso.

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

### D8 — não absorve a DETECÇÃO

`detect_pattern` acha PII em texto livre; este leitor aplica política a um valor **declarado**. São
perguntas diferentes em momentos diferentes, e a V2b do ADR da allowlist fechou exatamente o defeito
de tê-las fundidas.

⚠️ Registro de uma pergunta que este ADR **não** responde e que a medição levantou: o
`detect_pattern` de `credit_card` casa 16 dígitos sem separador, e mesmo assim a mensagem de saída
foi persistida crua no stream. **Por que a detecção não mascarou aquela mensagem** é assunto do
`adr-message-masking`, e está registrado como MSK-02.

---

## 3. Alternativas refutadas

| alternativa | por que não |
|---|---|
| **Filtrar por TAG** (a tag sensível nunca sai) | Reprovaria os 20, e 10 são legítimos (§1.3). Quebraria survey link e OTP, com o sintoma *"a mensagem chegou sem o link"* — que ninguém liga a mascaramento |
| **Segunda porta (`getMasked()` ao lado do `get()`)** | Duas portas, e a destrancada é a que vale. É o achado do `/sessions/{id}/stream`, verbatim |
| **Declarar a plateia em cada interpolação** | 392 declarações e 392 chances de esquecer; e a que faltar degrada para o permissivo |
| **Filtrar no adapter de canal** | Tarde demais: quando o texto chega lá, o valor **já está** no `pipeline_state` e no stream canônico — medido, 2 streams com o número cru. Filtrar na borda protege a tela e não o registro |
| **Mascarar tudo por default, sem plateia** | Quebra `invoke`: o CRM precisa do número inteiro. Um default único está errado para metade dos sítios |

---

## 4. Fases

**A ordem tem uma inversão deliberada: a EXCEÇÃO vem antes da APLICAÇÃO.** Aplicar primeiro
quebraria os 10 usos legítimos, e um arco que quebra o produto na primeira fase não chega à segunda.

| fase | entrega | por que aqui |
|---|---|---|
| **F0** | censo re-executável: interpolações por sítio × tipo × plateia derivada, com os 20 **classificados** (legítimo / defeito / sem plateia de cliente) | O número sozinho não diz de qual proposição é evidência (§1.3) |
| **F1** | o resolvedor de plateia + **modo auditoria**: calcula o que faria e LOGA, sem aplicar | Mesmo desenho da V3 da allowlist. É o que transforma "acho que são 10" em contagem |
| **F2** | a exceção declarada (D3): marca no template, greppável e auditada | Precisa existir **antes** da F3, senão o link quebra |
| **F3** | aplicar em `notify`/`menu` — o caminho de cliente | Onde estão os defeitos medidos |
| **F4** | `invoke` (confirmar que o cru é intencional e gateado) e depois `reason` (D5) | `reason` só depois de ter decisão própria |
| **F5** | `$.pipeline_state.*` via carimbo de proveniência (D7) | Fatia própria |

---

## 5. Gates

| gate | prova | testemunha negativa |
|---|---|---|
| `probe_ctx_read_audience_census.sh` | o censo da F0 reproduz os números, classificados | zero interpolações ⇒ INCONCLUSIVO, nunca verde |
| `probe_ctx_read_audience_resolve.sh` | a plateia derivada bate com o sítio nos quatro casos da D2 | sítio `agents_only` **não** pode derivar `cliente` |
| `probe_ctx_read_policy_applied.sh` | `credit_card` para plateia cliente não sai cru | o **mesmo** valor para plateia sistema (`invoke`) sai INTEIRO — senão o gate passa por bloquear tudo |
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
