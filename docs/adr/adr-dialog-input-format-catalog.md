# ADR — Catálogo de formatos de entrada no DialogForm (`format` nomeia um tipo; `pattern` sai)

**Status:** proposto
**Data:** 2026-09-04
**Componentes:** `packages/schemas`, `packages/config-api`, `packages/skill-flow-engine`,
`packages/mcp-server-plughub`, `packages/platform-ui`, `packages/channel-gateway`
**Relacionado:**
[`adr-masked-typed-declaration.md`](adr-masked-typed-declaration.md) *(precedente direto: `masked`
deixou de ser booleano e passou a NOMEAR um tipo do catálogo; este ADR aplica a mesma forma à
coleta, e **consome** o `formato` daquele catálogo em vez de repeti-lo)* ·
[`adr-contextstore-allowlist.md`](adr-contextstore-allowlist.md) *(criou `masking.types`)* ·
[`adr-skill-flow-editor-validation.md`](adr-skill-flow-editor-validation.md) *(de onde vem a frase
**afordância ≠ veredicto**, aqui reusada literalmente)* ·
[`adr-dialog-conditional-skip-logic.md`](adr-dialog-conditional-skip-logic.md) *(mesmo perímetro:
guarda declarativa, nunca control-flow em JSON)* ·
[`docs/product/dialog-primitive-and-runner-design.md`](../product/dialog-primitive-and-runner-design.md)
**Censo que o precede:** executado em 2026-09-04, números na §1. Falta portar para script
re-executável (F0).

---

## 0. Sumário da decisão

O campo de coleta de texto do `DialogForm` já sabe declarar domínio e comprimento
(`numeric`/`min_length`/`max_length`/`min`/`max`/`pattern`). O que ele **não** sabe é: guiar a
digitação, dizer *por que* recusou, e recusar `31/02/2026`. Nenhuma dessas três se resolve com
regex — a primeira porque regex não fala com o teclado, a segunda porque regex não tem rótulo, a
terceira porque calendário não é linguagem regular.

A decisão é fazer a declaração **nomear uma entrada de catálogo** — `format: "date_br"` — e o
catálogo carregar as duas metades que hoje não têm casa: **afordância** (máscara de digitação,
`inputmode`, `maxlength`, mensagem i18n) e **veredicto** (forma × validade semântica). `pattern`
**sai do schema**: tem zero usuários medidos, e removê-lo apaga por construção um fail-open que
hoje existe (regex inválida libera tudo, em silêncio) além de tirar código autorado por tenant do
event loop compartilhado do engine.

A regex não desaparece do sistema — **muda de autor**. Deixa de ser campo que o tenant preenche e
passa a ser a implementação de uma entrada de catálogo, escrita, revisada e testada uma vez.

Três coisas que este ADR deliberadamente **não** faz:

- **não cria um segundo catálogo ao lado de `masking.types`** (D3) — aquele já carrega
  `formato.display` para `cpf`, `credit_card`, `phone` e `card_expiry`; repetir a máscara seria
  duas casas afirmando o mesmo fato, e elas divergiriam;
- **não reusa `detect_pattern` como validador** (D4) — achar CPF em texto corrido e julgar se um
  campo É um CPF são perguntas diferentes, e a segunda não é regular;
- **não introduz control-flow** — `format` decide forma, nunca ramo. O perímetro do
  `adr-dialog-conditional-skip-logic` fica inteiro.

---

## 1. Contexto — o que foi medido

Medição de 2026-09-04, ambiente demo, 11 formas publicadas.

### 1.1 A declaração existe e é rica; a aplicação quase não existe

| camada | declara | aplica |
|---|---|---|
| `DialogValidation` na **pergunta** (`dialog.ts:56`) | ✅ 6 campos | — |
| `DialogValidation` no **campo** (`fields[]`) | ✅ mesmo objeto | ❌ **nunca chega a renderer nenhum** |
| editor (`DialogFormsPage.tsx:786-821`) | ✅ os 6 + `retry` | — |
| engine `menu` (`validateFormat`, `menu.ts:100`) | — | ⚠️ **só se `retry.max_attempts > 1`** |
| Console (`DialogFormRenderer.tsx`) | — | ❌ zero |
| página web (`survey_web.py`) | — | ❌ zero |

### 1.2 Quatro defeitos, todos da família *"promessa sem mecanismo"*

**(a) `validation` sem `retry` é no-op MUDO.** `menu.ts:155` —
`retryEnabled = maxAttempts > 1 && !!resolvedValidation`. O editor deixa marcar `numeric` + faixa
sem tocar em retry, e nada avisa. Medido ao vivo:

```
dialog_nps_v1          it=text  val={numeric, min:0, max:10}        retry=None   ← INERTE
dialog_otp_possession  it=text  val={numeric, len 6..6}             retry=3      ← roda
```

Duas formas declaram validação; **uma das duas não vale nada**, e a tela que a escreveu não diz.

**(b) `fields[].validation` é descartado no `form_get`.** `RenderField`
(`mcp-server-plughub/src/tools/dialog.ts:41`) é uma **allowlist** que não copia `validation`. Todo
`interaction: form` — aprovação, solicitação de limite, promoção de deploy — é estruturalmente
incapaz de validar. Segunda ocorrência da família DTO-01 (fechada em 2026-09-04 no editor), no
mesmo desenho de tipo espelhado.

**(c) Regex inválida falha ABERTA, em silêncio.** `menu.ts:104` —
`try { … } catch { /* invalid regex → skip */ }`, e o `catch` cai fora do `if`, então a função
segue e retorna `true`. Um `^[0-9{6}$` digitado errado no editor libera qualquer entrada, sem log.

**(d) Regex autorada por tenant roda no event loop compartilhado.** `new RegExp(v.pattern).test(s)`
no `skill-flow-service`, sem teto de passos. `(a+)+$` numa entrada longa é negação de serviço para
todos os fluxos do processo.

### 1.3 `pattern` tem zero usuários

| população | usa `pattern` |
|---|---|
| formas publicadas (dialog-api, ao vivo) | **0 de 11** |
| formas semeadas (`infra/dialog/*.json`) | **0 de 11** |
| YAMLs de skill (`MenuStep.validation`) | **0** |

Disponível desde 2026-07-07 e nunca adotado. Removê-lo é dívida que **morre contada**, não decreto.

### 1.4 Metade do catálogo já existe — e é o risco central deste ADR

`masking.types` (config-api, `seed.py:554`, espelhado em `DEFAULT_DATA_TYPE_CATALOG` de
`schemas/audit.ts`) já carrega máscara de exibição por tipo:

```
cpf          ###.###.###-##        credit_card  #### #### #### ####
phone        (##) #####-####       card_expiry  ##/##
```

Escrever `format.cpf.mask = "###.###.###-##"` num catálogo novo é **a mesma string em duas casas**.
Elas divergem — é o que sempre acontece — e o dia em que divergirem, a máscara que o cliente digita
e a máscara com que o dado é redigido no histórico deixam de ser a mesma, sem nada ficar vermelho.

### 1.5 Por que a política não pode morar em código

`evaluateAskWhen` é função **pura** em `@plughub/schemas` e mesmo assim tem **três**
implementações: a canônica, `evalAskWhen` (`DialogFormRenderer.tsx:95`) e `awEval`
(`survey_web.py:392`). Não foi desleixo — as três superfícies são um app React, um `<script>`
inline servido por Python e o engine em Node, e **não existe import que atravesse os três**.

Um catálogo cuja política morasse em código herdaria exatamente isso. É por isso que a D2 existe.

---

## 2. Decisões

### D1 — `format` nomeia uma entrada de catálogo; `pattern` sai do schema

```ts
// DialogValidation, depois
{
  format?:     string   // nome de entrada do catálogo — a declaração PRIMÁRIA
  required?:   boolean
  min_length?: number   // sobrepõe o do formato, nunca o afrouxa além do teto
  max_length?: number
  min?:        number
  max?:        number
  // numeric?:  ABSORVIDO por `format: "digits" | "integer" | "decimal"`
  // pattern?:  REMOVIDO — ver §1.3 (0 usuários) e §1.2(c)(d)
}
```

`numeric: true` continua aceito como **alias contado** de `format: "decimal"`, pelo mesmo tratamento
que o ADR da allowlist deu ao legado: alias com contador e data, nunca migração silenciosa. Ele tem
2 usuários vivos e sai quando o contador zerar.

**Por que nomear e não descrever.** É a mesma forma do `adr-masked-typed-declaration`: uma
declaração anônima (`masked: true`, ou uma regex crua) diz *o quê fazer* e não diz *sobre o quê* —
e tudo que depende de saber sobre o quê (rótulo do erro, teclado, dígito verificador, classe LGPD)
fica sem casa e acaba decidido por formulário. Conformidade por formulário significa N políticas de
CPF no tenant, e vale a mais frouxa.

### D2 — o catálogo é DADO; os avaliadores são INTERPRETADORES

A entrada de catálogo é um objeto serializável. Cada superfície tem um interpretador genérico,
pequeno e igual em forma:

```
entrada = {
  id, label_i18n_key,
  afordancia: { mask, inputmode, maxlength, placeholder_i18n_key },
  veredicto:  { shape_pattern, semantic, error_i18n_key },
  from_masked_type?: "cpf"        // ver D3
}
```

**Três interpretadores de uma tabela não são três tabelas.** A diferença com o `askWhen` é
material: lá a *política* está triplicada (mudar um operador exige três edições coerentes); aqui a
política é uma linha de config e o interpretador é boilerplate que não carrega decisão. É a forma
que `masking.types` já usa, com regex-como-dado consumida por runtimes diferentes.

**O interpretador não compila regex de fonte não confiável em runtime.** As entradas são
plataforma-autoradas; o `shape_pattern` é compilado uma vez no boot e cacheado. Isso é o que
fecha §1.2(d) — e fecha por construção, não por vigilância.

### D3 — o catálogo NÃO duplica `masking.types`: entradas de PII **referenciam**

Novo namespace config-api **`dialog.formats`**. Entradas não-PII (`date_br`, `time`, `digits`,
`integer`, `decimal`, `cep`) são autossuficientes. Entradas que correspondem a um tipo mascarado
declaram `from_masked_type: "cpf"` e **herdam a máscara** de `masking.types.cpf.formato.display` em
vez de reescrevê-la.

Isso não é só DRY: torna **provável por construção** que a máscara com que o cliente digita o CPF e
a máscara com que aquele CPF é redigido no histórico sejam a mesma string.

**Refutado: estender `masking.types` com os campos de entrada.** `date_br` e `digits` não são tipos
de mascaramento; entrariam ali exigindo uma classe LGPD e um bloco `mascara` que não têm — contêiner
largo para fato estreito, a invariante irmã da que este ADR aplica.

> ⚠️ Achado lateral, fora do escopo deste ADR mas registrado porque o censo o encontrou:
> `address`, `health` e `financial` declaram todas `display: "R$ #.##0,00"` em `masking.types`.
> Endereço não se exibe como moeda — é copy-paste. Não corrigir aqui (mexe em política de
> mascaramento, que tem dono próprio); registrar como tarefa.

### D4 — o veredicto tem DOIS níveis, e fundi-los é o erro que a regex convida

| nível | pergunta | quem responde |
|---|---|---|
| **forma** | os caracteres estão no arranjo certo? | `shape_pattern` (regex, ancorada) |
| **validade** | isto existe / fecha? | `semantic` (função nomeada do catálogo) |

`^\d{2}/\d{2}/\d{4}$` aceita `31/02/2026`. `^\d{3}\.\d{3}\.\d{3}-\d{2}$` aceita
`000.000.000-00`. **Uma forma pode ter só o primeiro nível** (`digits`, `cep`) — e declara isso,
em vez de deixar a ausência parecer aprovação.

**Corolário que precisa estar escrito, senão alguém "simplifica" depois:**
`masking.types.*.formato.detect_pattern` **não serve de `shape_pattern`**. Ele é um *finder* — não
ancorado, delimitado por `\b`, desenhado para achar PII no meio de texto corrido. Como validador
ele aceita `meu cpf é 111.222.333-44 obrigado`. A máscara é compartilhada (D3); **o veredicto não
é**.

### D5 — a validação passa a valer SEM `retry`; `retry` governa a REOFERTA

Hoje `retryEnabled` gateia a própria validação. Passa a ser:

- **sempre** valida quando há `format` (ou faixa/comprimento);
- `retry` decide o que acontece na falha: reofertar na mesma superfície (com `reprompt`) ou seguir
  direto para `on_failure`.

Sem isso, o campo mais restritivo é o que degrada — o oposto do que qualquer pessoa espera ao
marcar uma restrição.

**Isto muda comportamento de forma viva, e a população foi contada antes:** exatamente **uma**
(`dialog_nps_v1`, cuja `{numeric, 0..10}` passa a valer). Ela não tem consumidor de produção — a
única referência no repositório é `test_collect_masked_requirement.py`. Custo medido: um teste a
revisar.

### D6 — `fields[].validation` viaja: `RenderField` vira denylist

Mesmo conserto do DTO-01: um tipo espelhado que **enumera o que copiar** envelhece a cada campo
novo e transforma perda de dado em código que compila. Copia-se tudo menos o que é explicitamente
do outro modelo.

### D7 — afordância ≠ veredicto

A máscara, o `inputmode` e o `maxlength` **guiam**; não autorizam. O veredicto é do lado que recebe
a resposta. Uma superfície que só ganhou afordância **não está validada** — e é por isso que a F4
não pode ser confundida com "pronto".

Consequência que precisa ser dita: hoje o Console e a página web não têm veredicto nenhum, e depois
da F4 continuarão sem ele até a F3 alcançá-las. O plano é explícito sobre em que fase cada
superfície ganha o quê (§4).

---

## 3. Alternativas refutadas

| alternativa | por que não |
|---|---|
| **Regex crua como declaração do tenant** *(a ideia original)* | Não produz afordância (teclado, máscara, `maxlength`), não produz mensagem localizada, não alcança validade semântica (§D4), falha aberta com typo (§1.2c), e é código de terceiro no event loop (§1.2d). Além disso: **0 adoção em dois meses de disponibilidade** |
| **Dobrar formatos dentro de `masking.types`** | Contêiner largo para fato estreito — `date_br` teria de inventar classe LGPD e bloco `mascara` |
| **Catálogo novo, autossuficiente, com `cpf` repetido** | Segunda casa para a mesma máscara; divergem, e a divergência é muda (§1.4) |
| **Validar só no servidor** | Mata a afordância, que é metade do que foi pedido |
| **Atributo `pattern` do HTML nas telas** | Serve 2 das 3 superfícies; o engine (chat) não tem DOM. E deixaria o veredicto no cliente |
| **Catálogo em código (constante TS exportada)** | É a receita do `askWhen` triplicado (§1.5), e ainda tiraria a edição do tenant, contra *"every config field is UI-editable"* |

---

## 4. Fases

A ordem não é de esforço: cada fase deixa o sistema coerente, e nenhuma abre janela sem validação.

| fase | entrega | por que nesta ordem |
|---|---|---|
| **F0** | censo re-executável (`infra/test/q_dialog_format_census.sh`) com os números da §1 | Os números aqui foram medidos à mão. Achado sem instrumento volta a ser lembrança |
| **F1** | `dialog.formats` no config-api (seed + default em `@plughub/schemas`), com `from_masked_type` e as entradas iniciais | O catálogo precisa existir antes de alguém poder nomeá-lo |
| **F2** | engine passa a resolver `format` (`validateFormat` vira interpretador); **D5** (retry desacoplado) e **D6** (`fields[]`) entram junto | Antes de o editor escrever `format`, algo tem de saber lê-lo. `pattern` ainda vive aqui |
| **F3** | editor troca o campo livre de regex por seletor de formato; **`pattern` REMOVIDO** do schema | Só agora, com leitor de pé e 0 usuários confirmados no re-censo da F0 |
| **F4** | afordância + veredicto no Console e na página web (interpretador da D2 nas duas) | É onde o wrap-up e a aprovação vivem, e onde hoje não há nada |
| **F5** | gates (§5) | — |

**F2 antes de F3** é o ponto que não pode inverter: remover `pattern` antes de o engine entender
`format` abriria uma janela sem validação nenhuma.

---

## 5. Gates

Cada um com controle positivo — a falta dele é o que faz *"tudo foi preservado"* e *"não havia o
que preservar"* darem o mesmo verde.

| gate | prova | testemunha negativa |
|---|---|---|
| `probe_dialog_format_catalog.sh` | toda entrada resolve máscara e veredicto; `from_masked_type` casa com `masking.types` | entrada com `from_masked_type` inexistente ⇒ REPROVA |
| `probe_dialog_format_verdict.sh` | `31/02/2026` recusado por `date_br`; `000.000.000-00` recusado por `cpf` | data válida aceita — senão o gate passa por recusar tudo |
| `probe_dialog_validation_applies.sh` | validação vale **sem** `retry` (D5) | forma sem `validation` ⇒ INCONCLUSIVO, nunca verde |
| `probe_dialog_field_validation_travels.sh` | `fields[].validation` sobrevive ao `form_get` (D6) | campo sem validation round-trip limpo |
| `probe_dialog_format_surfaces.sh` | as três superfícies dão o **mesmo** veredicto para a mesma entrada | uma superfície divergindo ⇒ REPROVA nomeando qual |

O último é o que importa mais: ele é a única coisa que impede a D2 de virar, com o tempo, o
`askWhen` de novo.

---

## 6. Dívida que este ADR NÃO fecha

- **`masking.types`: `display` de `address`/`health`/`financial`** — copy-paste de moeda (§D3).
  Tarefa própria, dono próprio.
- **Teto de execução do interpretador** — as entradas são plataforma-autoradas, então o vetor de
  ReDoS cai por autoria (§D2), não por limite. Se um dia o catálogo virar tenant-editável em regex,
  o teto passa a ser pré-requisito, e não detalhe.
- **`platform-ui` sem runner de teste** — a metade de UI da F4 fica sem gate automatizado, como o
  resto do pacote. Ausência declarada, não coberta por `grep`.
