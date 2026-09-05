# ADR: Skip-logic condicional em DialogForm — guarda declarativa `ask_when` (não control-flow)

**Status:** Aceito + implementado (2026-07-08). Fases 1–4 (schema+avaliador, runtime/loop, veículo web, editor)
prontas e **validadas ao vivo** no webchat (guarda `atendimento < 3` pergunta/pula o follow-up). Ver CHANGELOG.
**Data:** 2026-07-08
**Componentes:** `packages/schemas` (`dialog.ts` — guarda no nó/bloco), `packages/skill-flow-engine` (step `loop`
+ avaliador de guarda), `packages/channel-gateway` (`survey_web.py` — mesma guarda em JS), `mcp-server-plughub`
(`survey_record`/`composeScore` — pulada = NA, sem mudança), `packages/platform-ui` (editor — construtor de
condição, front à parte).
**Relacionado:** `docs/adr/adr-survey-form-scoring-composition.md` (composição/NA),
`docs/adr/adr-otp-workflow-and-dialog-primitive.md` (4 costuras; primitivo linear),
`packages/schemas/src/dialog.ts` (`DialogForm` linear, sem `next` condicional),
`packages/schemas/src/skill.ts` (`ChoiceStep` — control-flow do skill), TODO § "Flow — step de expressão
sandboxed (NÃO eval cru)".

---

## Contexto

Perguntas de follow-up condicionais (skip-logic — "se a nota for baixa, aprofunde") são padrão em qualquer
instrumento de pesquisa. Hoje o interpretador executa **todos** os nós na sequência, incondicionalmente. O
`DialogForm`, por invariante, é **conteúdo linear sem `next` condicional**: branching é **controle**, do skill
chamador, não do JSON — para não transformar o form numa linguagem de fluxo, manter o runner burro
(single-turn), e preservar a costura conteúdo×controle (mesmo conteúdo servível em chat/inline/web).

A proposta original ("comando `test on` que testa `response` e executa um bloco") é **imperativa** e
transbordaria controle para o JSON — o começo da ladeira "linguagem em JSON" que o design fechou. A decisão
abaixo entrega a mesma capacidade numa forma **declarativa e limitada** que preserva o invariante.

## Decisões

### D1 — Guarda declarativa `ask_when`, não comando imperativo

Adicionar uma **guarda opcional** `ask_when` a nós e blocos. O runner permanece **linear**: apenas **pula** o
nó/bloco cuja guarda for falsa. Não há `test on`, `goto`, `else`, aninhamento nem loop no form — não é
control-flow, é **skip-logic de conteúdo** (qual conteúdo mostrar). Ausência de `ask_when` = sempre executa
(retrocompatível: forms atuais não mudam).

### D2 — Guarda no bloco E na pergunta

`ask_when` vale tanto no **bloco** (caso comum: seção de follow-up inteira condicional) quanto na **pergunta**
(grão fino). **Precedência:** se a guarda do bloco é falsa, o bloco inteiro é pulado (guardas por-pergunta nem
são avaliadas); se verdadeira, as guardas por-pergunta ainda se aplicam. Statements (falas) também aceitam
`ask_when`.

### D3 — Expressão declarativa `{ field, op, value }` (sandboxed, sem eval)

A condição reusa o vocabulário que o engine já interpreta (estilo `ChoiceStep`), **não** uma nova linguagem:

- `field` — o `output_key` de uma pergunta **anterior** (referência estável; **nunca** um `response` mutável).
- `op` ∈ `{ lt, lte, gt, gte, eq, ne, in }`.
- `value` — escalar (ou lista, para `in`).

Ex.: `ask_when: { field: "csat", op: "lt", value: 3 }`. **Pura, determinística, sem I/O nem side-effect** —
alinhada à decisão "step de expressão sandboxed (NÃO eval cru)" do TODO. **Referência só para trás:** `field`
deve apontar a uma pergunta **anterior** na ordem linear; forward-reference é erro de validação (deploy/editor).

### D4 — Mesma guarda nos dois veículos

- **Chat (runner):** o step `loop` avalia `ask_when` contra o acumulador de respostas e **pula** o item falso.
- **Web (`/survey/{token}`):** o renderizador avalia a **mesma** condição em JS (mostrar/esconder). Mesma
  semântica, dois renderizadores. Um avaliador puro compartilhado (`@plughub/schemas`) evita drift.

### D5 — Pulada = NA = re-normalizada (composição inalterada)

Uma pergunta pulada = não respondida = **NA**; o `composeScore` já **re-normaliza NA** (ADR de composição).
Skip-logic compõe a nota corretamente **de graça** — nada novo no cálculo.

### D6 — A linha conteúdo×controle permanece

`ask_when` no form = **qual conteúdo mostrar**. **Agir** (delegar, escalar, chamar ferramenta, rotear) continua
sendo controle, no `choice`/`escalate` do skill-flow — nunca no form. Se um follow-up precisar *agir* (não só
perguntar), é skill, não guarda.

## Decisões em aberto

1. **`field` referencia pergunta não respondida** (pulada por outra guarda, ou ainda não alcançada apesar da
   validação de forward-reference — ex.: a driver foi pulada). **Proposta:** resposta ausente ⇒ guarda **falsa**
   (conservador: não aprofunda se o driver não foi respondido). Confirmar no ADR de implementação.
2. **`checklist`/multi-valor:** resposta multi-seleção é array. **Proposta:** `in` = teste de pertinência;
   `lt/gt/…` indefinidos para array ⇒ guarda falsa. Ou adiar suporte a checklist como `field`.
3. **UX do editor** (construtor da guarda — linha "só perguntar se… [pergunta anterior] [op] [valor]") —
   trabalho de front à parte; **não bloqueia** schema/runtime. Entra junto da 2ª passada de UX do editor.

## Consequências

- Skip-logic autorável **no form** (pelo editor, sem tocar skill) — essencial para o produto de pesquisas, onde
  o autor não escreve skills.
- Invariante das 4 costuras preservado: o form continua **conteúdo linear**; a guarda é filtro declarativo
  bounded, não control-flow.
- Runtime bounded (sem eval), reusando o vocabulário de condição existente; um avaliador puro compartilhado
  entre engine e web.
- Retrocompatível: `ask_when` ausente = comportamento atual.
- Custo: avaliador em dois veículos (loop step + web) + validação de forward-reference + UX de editor (à parte).


---

## Apêndice — resumo denso migrado do índice do `CLAUDE.md` (2026-08-31)

> Este bloco vivia como **uma linha** do índice `docs/` no `CLAUDE.md`, onde ocupava 982 bytes.
> Medido antes de mover: **~85% do seu vocabulário já existe neste ADR** — ele é uma condensação
> independente, não uma cópia, e por isso os ~15% restantes (achados, números e nomes de arquivo que
> só foram registrados no índice) **não existiam em lugar nenhum além dali**. Movido inteiro, sem
> resumir, porque a alternativa — cortar no CLAUDE.md e confiar que o ADR já dizia tudo — perderia
> exatamente a fração que não dá para recuperar.
>
> **É trabalho aberto**, não documentação final: a fração nova deve ser dobrada no corpo do ADR e
> este apêndice, encolhido. Enquanto isso não acontece, ele é a única cópia.

skip-logic condicional em DialogForm (guarda declarativa `ask_when`, **não** control-flow) — **Aceito + implementado 2026-07-08**, validado ao vivo no webchat *(corrigido 2026-08-17: este índice dizia "proposto" por mais de um mês)*. **Guarda LOAD-BEARING**, e a razão mudou sem enfraquecer *(2026-08-18)*: a versão anterior dizia "com o editor de fluxo local saindo (interop n8n)" — o editor **fica**, e a pressão para empurrar control-flow ao form **existe do mesmo jeito**, agora vinda do lado oposto (enquanto o editor de fluxo próprio for insuficiente, o formulário é o caminho de menor resistência). Se ceder, o editor de fluxo é reconstruído dentro do editor de formulário, com linguagem pior. Avaliador canônico `evaluateAskWhen` em `schemas/src/dialog.ts:423`, **hoje triplicado** (espelhos em `survey_web.py:386` e `DialogFormRenderer.tsx:400`) — e desde 2026-09-05 **CONFERIDO**: `infra/test/probe_ask_when_parity.sh` recorta as tres do fonte real e exige veredicto identico, com o ramo C lendo a lista de ops do proprio `switch` canonico (op novo nasce cobrado). Unificar nao esta disponivel — o platform-ui nao importa `@plughub/schemas` e a copia da web e JS dentro de uma string Python. ⚠️ O VEREDICTO coincide; a CONSEQUENCIA nao: resposta de no pulado e apagada na web e mantida no Console (DLG-10 no `pending.md`). Aberta só 1 das 3 decisões do ADR (`checklist` multi-valor)
