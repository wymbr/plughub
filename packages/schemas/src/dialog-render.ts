/**
 * dialog-render.ts
 * Normalização de uma `DialogForm` no bloco `render` que o runner consome —
 * e o VEREDICTO estrutural que o editor pergunta antes de salvar.
 *
 * ── Por que mora aqui, e não no mcp-server ──────────────────────────────────
 *
 * Até 2026-09-04 esta função vivia dentro de `tools/dialog.ts` (mcp-server), a
 * casa do `form_get` — o único consumidor que existia. Com o editor JSON do
 * platform-ui aparece um SEGUNDO consumidor que precisa da MESMA normalização
 * (o preview mostra o que o runner receberia), e a alternativa seria uma cópia
 * no browser. Duas implementações da mesma regra divergem, e a divergência
 * apareceria como *"o preview mostrou uma coisa e o cliente viu outra"*.
 *
 * O próprio `resolveLocalizedText` já antecipava este consumidor: *"kept here so
 * the runner, the editor preview and any renderer resolve identically"*.
 *
 * ⚠️ O platform-ui **não importa `@plughub/schemas`** (sem workspaces, o
 * Dockerfile copia só o pacote, e há risco de dual-instance de Zod — ver
 * `SkillFlowsPage.tsx` § dry-run e `adr-skill-flow-editor-validation`). Logo o
 * editor não chama estas funções direto: ele PERGUNTA ao servidor
 * (`POST /api/dialog/preview`), que roda exatamente o que o `form_get` roda.
 * A regra do ADR vale igual aqui: AFORDÂNCIA no cliente, VEREDICTO no servidor.
 *
 * Conteúdo apenas — nenhum control flow. Branching é do skill chamador.
 */

import type {
  DialogForm,
  DialogOption,
  QuestionNode,
} from "./dialog"
import {
  DialogFormSchema,
  askWhenForwardRefErrors,
  optionTreeIssues,
  resolveLocalizedList,
  resolveLocalizedText,
} from "./dialog"

// ─── Render normalization (§18.4) ─────────────────────────────────────────────
// Flatten a DialogForm into a single-turn render block the dialog-runner menu
// consumes directly: leading statements → menu prompt, questions → form fields,
// trailing statements → statement_after, plus a domain-blind capture echo.
// Content-shaping only (no control flow).

export interface RenderField {
  id: string; label: string; type: string; required: boolean; masked: boolean | string
  // Approval (ADR adr-human-approval-workflow-step): pre-filled editable value +
  // per-field options (select). Absent for plain capture-only survey fields.
  value?:   string | number | boolean
  options?: RenderOption[]
  /**
   * D6 do ADR do catálogo de formatos. Até 2026-09-04 este campo NÃO existia e a
   * construção abaixo era uma ALLOWLIST — logo `fields[].validation` era
   * descartado aqui, e todo `interaction: "form"` (aprovação, solicitação de
   * limite, promoção de deploy) era estruturalmente incapaz de validar, sem
   * nada ficar vermelho. Segunda ocorrência da família DTO-01, no mesmo desenho
   * de tipo espelhado.
   */
  validation?: unknown
}
/**
 * RenderOption — uma opção e, quando há, a SUBÁRVORE dela (F3 do
 * `adr-dialog-tree-options`).
 *
 * Até a F3 o mapeamento era `{id, label}` e **descartava os filhos**. Uma forma
 * com taxonomia chegava à superfície como uma lista PLANA de raízes, e escolher
 * "Financeiro" gravava a PASTA como resposta — dado errado, sem nada vermelho.
 * Perder subárvore em silêncio é o modo de falha que a D4 já recusava no schema;
 * aqui ele reaparecia um andar abaixo.
 *
 * `active: false` (D6) é filtrado AQUI: a folha aposentada sai da OFERTA e
 * permanece no form, para o histórico continuar explicável.
 */
export interface RenderOption {
  id: string; label: string; options?: RenderOption[]
  /** D2 do `adr-tree-return-continuation.md` — a question de continuação desta folha.
   *  Viaja no render porque quem decide o que fazer no retorno é o CHAMADOR, e ele lê
   *  a árvore pelo render, nunca o form cru. */
  on_return?: string
  /**
   * ORQ-15 — o que a opção COBRE, já resolvido na língua pedida: a segunda linha do
   * menu. Ausente quando o autor não escreveu (ou escreveu só espaço) — nunca `""`,
   * para o canal não desenhar uma linha vazia.
   *
   * ⚠️ `examples` NÃO viaja aqui, e isso é o contrato: o `render` vai aos canais, e
   * frase de classificador chegaria a um adapter que a exibe. Quem precisa dos
   * exemplos lê o `DialogOption` cru (`leafMeanings`).
   */
  description?: string
}
// Retry affordance flattened for the menu step: reprompt localized, counter fixed.
export interface RenderRetry { reprompt: string; max_attempts: number }
export interface RenderQuestion {
  /**
   * `id` do NODE — não confundir com `output_key`, que nomeia a RESPOSTA.
   *
   * Passou a viajar na RET-02 porque `on_return` (D2 do ADR do retorno) aponta
   * para o id do node, e sem ele a projeção de árvore só sabia endereçar por
   * `output_key`: o ponteiro validado por `returnRefErrors` seria inendereçável
   * pela própria tool que o consome.
   */
  id:          string
  prompt:      string
  interaction: string
  options:     RenderOption[]
  output_key:  string
  capture:     unknown
  visibility:  unknown
  validation:  unknown               // format-only validation (numeric/pattern/…) or undefined
  retry:       RenderRetry | undefined  // reprompt (localized) + max_attempts, or undefined
  ask_when:    unknown               // declarative skip-logic guard { field, op, value } or undefined
}
export interface DialogRender {
  // §17.4 — single-question NATIVE view (the v1 render the runner uses):
  interaction: string                 // the question's native interaction (text|button|list|...)
  prompt:      string                 // leading statements + the question prompt, localized
  options:     RenderOption[]         // the question's options (localized labels), for button/list
  output_key:  string                 // where the raw answer keys (domain reads payload.value)
  visibility:  unknown                // the question's visibility (enum|array with @ctx refs) or "all"
  validation:  unknown                // the question's format validation, or undefined
  retry:       RenderRetry | undefined // the question's retry (reprompt localized + max_attempts)
  timeout_s:   number                 // §21 — the question's timeout (s); menu step reads via ref
  // Fatia 2 loop view: one entry per question (walked sequentially by a `loop` step).
  questions:       RenderQuestion[]
  // Legacy/multi-field view (interaction=form): one field per question.
  menu_prompt:     string
  fields:          RenderField[]
  statement_after: string
  captures:        Record<string, unknown>
  // NIV-04 fatia A — projeção POR NÓ: `node_id → texto resolvido`. Statements dão
  // o próprio texto; questions dão o `prompt`.
  //
  // POR QUE ELA EXISTE. O `render` acima é single-turn: statements só existem como
  // satélites de uma pergunta (`menu_prompt` os junta com `\n\n`). Isso serve o
  // dialog-runner, e **não serve** o roteiro de um fluxo de agente — cujos avisos
  // estão espalhados por ramos diferentes (saudação, transferência, encerramento).
  // Sem endereçamento por nó, migrar roteiro para `DialogForm` exigiria uma forma
  // por aviso: medido em 2026-09-03, **79 pontos estáticos em 24 skills** virariam
  // ~79 formas e ~79 `invoke` novos, quase dobrando a contagem de steps.
  //
  // Com `by_node`, o fluxo carrega **uma** forma (o seu roteiro) num `invoke` só e
  // cada `notify` referencia o seu nó.
  //
  // ⚠️ **O texto NÃO é re-interpolado.** O `interpolate` do engine é de PASSE ÚNICO:
  // ele coleta os `{{…}}` do template ORIGINAL, resolve e substitui — um valor
  // inserido que contenha `{{…}}` chega ao cliente com as chaves literais. Logo nó
  // com texto dinâmico ainda NÃO migra; são 20 pontos, contados, e a decisão sobre
  // uma segunda passada tem vetor próprio (quem edita conteúdo passaria a poder
  // injetar referências ao `pipeline_state`).
  by_node:         Record<string, string>
  /**
   * F3/D11 — a forma EXIGE uma superficie que desenhe arvore. DERIVADO da
   * presenca de subarvore, nunca declarado pelo autor: campo pode ser esquecido,
   * a estrutura nao.
   *
   * Quem consome decide o que fazer com um `true` que nao sabe desenhar — e a
   * unica resposta aceitavel e RECUSAR ALTO. Achatar `Financeiro > Cobranca
   * indevida` numa lista de 40 botoes e emulacao muda: entrega uma tela que
   * parece certa e perde a hierarquia que a serie do Arc 12 vai contar.
   */
  options_tree:    boolean
}

// Flatten a question's retry (LocalizedText reprompt → string) for the menu step.
/** Mapeia opções resolvendo i18n e PRESERVANDO a subárvore; descarta aposentadas. */
function mapOptions(
  opts: ReadonlyArray<{ id: string; value?: string; label: unknown; options?: unknown; active?: boolean; on_return?: string; description?: unknown }> | undefined,
  locale: string | undefined,
  dl: string,
): RenderOption[] {
  return (opts ?? [])
    .filter(o => o.active !== false)
    .map(o => {
      const filhos = Array.isArray(o.options)
        ? mapOptions(o.options as Parameters<typeof mapOptions>[0], locale, dl)
        : []
      const ro: RenderOption = {
        id:    o.value ?? o.id,
        label: resolveLocalizedText(o.label as never, locale, dl),
      }
      // Pasta que ficou VAZIA por aposentadoria vira folha selecionável — e o
      // rótulo viraria resposta. Emitir `options: []` seria pior (a superfície
      // abriria uma coluna vazia), então a chave só existe quando há filho.
      if (filhos.length) ro.options = filhos
      // D2 do ADR do retorno: o ponteiro de continuação viaja para quem decide
      // o que fazer no retorno — o CHAMADOR, que lê a árvore pelo render.
      if (o.on_return) ro.on_return = o.on_return
      // ORQ-15: mesma resolução do `leafMeanings` (trim, vazio = ausente), para o
      // cliente ler exatamente o que o classificador leu. Cópia de regra CONFERIDA
      // pelo gate `probe_orq15_option_description.sh`.
      if (o.description !== undefined) {
        const d = resolveLocalizedText(o.description as never, locale, dl).trim()
        if (d) ro.description = d
      }
      return ro
    })
}

/** True quando ALGUMA opção tem subárvore — derivado, nunca declarado pelo autor. */
function temArvore(opts: ReadonlyArray<RenderOption>): boolean {
  return opts.some(o => (o.options?.length ?? 0) > 0)
}

/**
 * Um NIVEL da arvore de opcoes, projetado a partir de um caminho.
 *
 * Existe porque navegar uma arvore com o CLIENTE e turno-a-turno: o `menu` step
 * mostra uma lista PLANA por vez, enquanto o `render` traz a arvore inteira
 * aninhada. Sem esta projecao, apontar a ref do menu para o render entregaria so o
 * nivel de cima e os filhos ficariam invisiveis — o mesmo achatamento mudo que o
 * adapter de WhatsApp produz, por outro caminho.
 */
export interface TreeLevel {
  /**
   * O caminho RESOLVEU? ⚠️ Segmento desconhecido devolve `false` com `options`
   * VAZIO — nunca a raiz. Degradar para a raiz seria o valor plausivel mais barato
   * de produzir aqui: a tela voltaria ao menu principal e pareceria certa, com o
   * cliente perdendo a navegacao sem que nada ficasse vermelho.
   */
  found:    boolean
  /**
   * `on_return` do NÓ DO CURSOR (D2 do `adr-tree-return-continuation.md`) — a
   * question que o chamador executa quando o agente desta folha devolver.
   *
   * ⚠️ É do nó do CURSOR, nunca dos filhos: quem continua é a folha ESCOLHIDA.
   * Ausente ⇒ a folha encerra o fluxo do chamador, que é o comportamento de hoje.
   */
  on_return?: string
  /**
   * O no DO CAMINHO e folha (selecionavel). DERIVADO da ausencia de filhos — a
   * mesma D2 do `adr-dialog-tree-options`, e por isso pasta esvaziada por
   * aposentadoria JA chega aqui como folha (o `mapOptions` omite `options` vazio).
   */
  is_leaf:  boolean
  /** Filhos do no do caminho, UM nivel, sem aninhamento. Vazio quando folha. */
  options:  RenderOption[]
  /** O caminho consultado, ecoado — o chamador guarda o cursor, esta funcao nao. */
  path:     string[]
}

/**
 * Projeta UM nivel da arvore de opcoes a partir de um caminho de ids.
 *
 * Pura e sem estado: o cursor e do chamador. `path` vazio devolve a raiz.
 *
 * ⚠️ **O gemeo do Console NAO pode ser este.** `platform-ui` nao depende de
 * `@plughub/schemas` (sem workspaces, risco de dual-instance de Zod), entao o
 * `nivelDe` do `DialogFormRenderer` continua sendo copia — TOPOLOGIA, como o
 * `evaluateAskWhen` triplicado, nao desleixo. O que os separa alem disso e o
 * INSUMO: la se caminha o `DialogOption` cru (chaveado por `value ?? id`), aqui o
 * `RenderOption` ja normalizado.
 */
export function optionsAtPath(
  roots: ReadonlyArray<RenderOption>,
  path:  ReadonlyArray<string>,
): TreeLevel {
  const trilha = [...path]
  let nivel: RenderOption[] = [...roots]
  let atual: RenderOption | undefined

  for (const passo of trilha) {
    const achado = nivel.find(o => o.id === passo)
    if (!achado) return { found: false, is_leaf: false, options: [], path: trilha }
    atual = achado
    nivel = achado.options ?? []
  }

  // Raiz (caminho vazio) nao e um no: nao e folha, e os filhos sao as raizes.
  if (!atual) return { found: true, is_leaf: false, options: nivel, path: trilha }
  const saida: TreeLevel = { found: true, is_leaf: nivel.length === 0, options: nivel, path: trilha }
  if (atual.on_return) saida.on_return = atual.on_return
  return saida
}

/**
 * Todos os caminhos de FOLHA sob `roots`, pontuados e em ordem de autoria.
 *
 * É o **vocabulário de desfechos permitidos** — a D6 do
 * `adr-orchestrator-tree-navigation`: com LLM quem navega é o LLM, mas ele tem de
 * **aterrissar numa folha declarada**. Sem isso o roteador é inauditável, e os dois
 * orquestradores (determinístico e com LLM) mediriam em unidades diferentes — não
 * haveria como provar que um roteia melhor que o outro.
 *
 * ⚠️ **Serve a duas metades que não se substituem**: alimentar o prompt E conferir a
 * resposta. Mandar a lista ao LLM sem conferir o que ele devolve é promessa sem
 * mecanismo — a família do DDL de `participation_intervals`. A conferência usa a
 * MESMA projeção (`optionsAtPath` com o caminho pontuado), não uma segunda leitura.
 *
 * Pasta com `options` vazio conta como FOLHA, pela D2: folha × pasta é derivado de
 * *"tem filhos?"*, nunca de um campo declarado.
 */
export function leafPaths(roots: ReadonlyArray<RenderOption>): string[] {
  const saida: string[] = []
  const anda = (opts: ReadonlyArray<RenderOption>, trilha: string[]): void => {
    for (const o of opts) {
      const aqui = [...trilha, o.id]
      const filhos = o.options ?? []
      if (filhos.length === 0) saida.push(aqui.join("."))
      else anda(filhos, aqui)
    }
  }
  anda(roots, [])
  return saida
}

/** ORQ-12 — o SIGNIFICADO de uma folha, para o classificador. */
export interface LeafMeaning {
  /** Caminho pontuado, idêntico a um item de `leafPaths` (prefixado pela trilha). */
  path:         string
  label:        string
  description?: string
  /** Só o classificador lê; nunca é exibido. */
  examples?:    string[]
}

/**
 * ORQ-12 — o vocabulário de `leafPaths` COM significado: rótulo, descrição e
 * exemplos de cada folha sob `path`, na língua pedida.
 *
 * Lê o `DialogOption` CRU, e não o `RenderOption`, de propósito: `examples` não
 * pode entrar no `render`, que vai aos canais — seria texto de classificador
 * chegando a um adapter que o exibe. Por isso a caminhada REPETE as duas regras
 * do `mapOptions` (aposentada sai; id = `value ?? id`; pasta esvaziada vira
 * folha), e o chamador confere que os caminhos batem com `leafPaths` — duas
 * leituras do mesmo fato só são aceitáveis CONFERIDAS.
 */
export function leafMeanings(
  roots:   ReadonlyArray<DialogOption> | undefined,
  path:    ReadonlyArray<string>,
  dl:      string,
  locale?: string,
): LeafMeaning[] {
  const ativos = (l: ReadonlyArray<DialogOption> | undefined) => (l ?? []).filter(o => o.active !== false)
  let nivel = ativos(roots)
  for (const passo of path) {
    const achado = nivel.find(o => (o.value ?? o.id) === passo)
    if (!achado) return []
    nivel = ativos(achado.options)
  }
  const saida: LeafMeaning[] = []
  const anda = (opts: DialogOption[], trilha: string[]): void => {
    for (const o of opts) {
      const aqui = [...trilha, o.value ?? o.id]
      const filhos = ativos(o.options)
      if (filhos.length) { anda(filhos, aqui); continue }
      const m: LeafMeaning = { path: aqui.join("."), label: resolveLocalizedText(o.label, locale, dl) }
      if (o.description !== undefined) {
        const d = resolveLocalizedText(o.description, locale, dl).trim()
        if (d) m.description = d
      }
      const ex = resolveLocalizedList(o.examples, locale, dl).map(x => x.trim()).filter(Boolean)
      if (ex.length) m.examples = ex
      saida.push(m)
    }
  }
  anda(nivel, [...path])
  return saida
}

function flattenRetry(q: QuestionNode, locale: string | undefined, dl: string): RenderRetry | undefined {
  if (!q.retry) return undefined
  return {
    reprompt:     resolveLocalizedText(q.retry.reprompt, locale, dl),
    max_attempts: q.retry.max_attempts,
  }
}

/**
 * Question de ENTRADA do form — D3 do `adr-tree-return-continuation.md`.
 *
 * A convenção é `id: "main"`; na ausência dela, a PRIMEIRA question, que é o
 * comportamento de sempre. Por isso as 14 formas publicadas (5 delas com mais de
 * uma question) continuam resolvendo exatamente o que resolviam.
 *
 * ⚠️ É convenção e não campo porque `QuestionNode.id` **já existe** — inventar um
 * `entry` no form seria uma segunda fonte para um fato que o id já carrega.
 */
export function entryQuestionId(form: DialogForm): string | undefined {
  const qs = form.nodes.filter(n => n.kind === "question")
  const main = qs.find(q => q.id === "main")
  return (main ?? qs[0])?.id
}

/**
 * `category_path` — a MEDIÇÃO do caminho, que **não** é o mesmo que o endereço.
 *
 * ── Por que são duas coisas, e confundi-las custou um contato real ───────────
 *
 * `path` e `leaves` são **ENDEREÇO**: voltam para dentro da própria tool como
 * `input.path` / `chosen_id`, e `optionsAtPath` caminha o `options` da question
 * corrente. Prefixá-los com o id da question faria a projeção procurar uma opção
 * chamada `pos_atendimento` na raiz, não achar, e a navegação **reiniciaria
 * parecendo certa** — é o mesmo modo de falha que o split por ponto da F2 existe
 * para impedir.
 *
 * `category_path` é **MEDIÇÃO**: é a `category` do Arc 12 e é a chave de
 * `navigation_pools`. A D5 do `adr-tree-return-continuation.md` decide que a
 * continuação é **raiz própria** — `pos_atendimento.especialista`, nunca
 * `sac.info_plano.pos_atendimento.especialista` — para que a série separe *"o que
 * o cliente pediu ao entrar"* de *"o que pediu depois de ser atendido"*.
 *
 * ⚠️ **Medido em contato real (2026-09-07):** a tool compunha `path.join(".")`
 * para toda question, então a continuação media `outra_coisa` enquanto o fluxo e o
 * `navigation_pools` comparavam com `pos_atendimento.outra_coisa`. Nenhum dos dois
 * estava errado sozinho — **eles compunham o mesmo caminho em duas casas** e só
 * uma era o runtime. Consequência: o comando não casava, o caminho seguia como
 * DEMANDA, `pool_route_resolve` recusava alto (sem default, por decisão) e o
 * contato caía na fila humana. E *"falar com um especialista"* chegava ao destino
 * certo **pelo motivo errado** — pela falha de rota, não pelo mapa.
 *
 * A raiz da continuação é o **id da question**, porque é ele que o `on_return`
 * nomeia — nada de campo novo.
 */
export function categoryPathFor(
  entryId:    string | undefined,
  questionId: string | undefined,
  path:       ReadonlyArray<string>,
): string {
  const cru = path.join(".")
  // A question de ENTRADA não prefixa: `sac.info_plano` é o que a série já mede,
  // e prefixá-la renomearia todo o histórico do Arc 12 num deploy.
  if (!questionId || questionId === entryId) return cru
  return cru ? `${questionId}.${cru}` : questionId
}

/**
 * Monta o render de UM turno.
 *
 * `fromQuestionId` (D4 do ADR do retorno) escolhe de qual question partir — é
 * assim que o chamador renderiza a question de continuação apontada por
 * `on_return`. Ausente ⇒ **comportamento idêntico ao de sempre**.
 *
 * ⚠️ **A compatibilidade aqui é medida, não presumida.** Sem `fromQuestionId` o
 * `before`/`after` continua sendo *"tudo antes / tudo depois da PRIMEIRA
 * question"*. A janela por bloco (statements desde a question anterior até a
 * seguinte) vale **só** quando alguém pede uma question específica — e ela é
 * diferente: medido em 2026-09-06, **5 das 14 formas publicadas têm mais de uma
 * question** (survey e wrap-up), e aplicar a janela nova a todas mudaria o
 * `statement_after` delas em silêncio.
 *
 * ⚠️ `fromQuestionId` que não existe **cai no comportamento padrão**, nunca em
 * render vazio: quem recusa ponteiro quebrado é `returnRefErrors`, na validação,
 * onde há alguém para ler o erro. Render vazio aqui seria um aviso em branco na
 * cara do cliente.
 */
export function buildRender(form: DialogForm, locale?: string, fromQuestionId?: string): DialogRender {
  const dl = form.default_locale
  const before: string[] = []
  const after:  string[] = []
  const fields: RenderField[] = []
  const questions: RenderQuestion[] = []
  const captures: Record<string, unknown> = {}
  const byNode: Record<string, string> = {}
  let seenQuestion = false
  let firstQuestion: QuestionNode | null = null

  // Índice da question ALVO e a janela de statements que lhe pertence. Só existe
  // quando o chamador pede uma question específica (ver o cabeçalho).
  const idxAlvo = fromQuestionId
    ? form.nodes.findIndex(n => n.kind === "question" && n.id === fromQuestionId)
    : -1
  const janela = (() => {
    if (idxAlvo < 0) return null
    let ini = 0
    for (let k = idxAlvo - 1; k >= 0; k--) {
      if (form.nodes[k]!.kind === "question") { ini = k + 1; break }
    }
    let fim = form.nodes.length
    for (let k = idxAlvo + 1; k < form.nodes.length; k++) {
      if (form.nodes[k]!.kind === "question") { fim = k; break }
    }
    return { ini, fim }
  })()

  for (const node of form.nodes) {
    // Fora da janela do bloco pedido: o nó ainda entra em `questions[]` e
    // `by_node` (que descrevem a FORMA inteira), mas não no turno renderizado.
    const iNo = form.nodes.indexOf(node)
    const foraDaJanela = janela !== null && (iNo < janela.ini || iNo >= janela.fim)
    if (node.kind === "statement") {
      const txt = resolveLocalizedText(node.text, locale, dl)
      byNode[node.id] = txt
      // ⚠️ Com janela o discriminador é a POSIÇÃO relativa ao alvo, nunca o
      // `seenQuestion` global: a question de ENTRADA vem antes e já teria virado
      // a chave, jogando para `after` um statement que abre o bloco pedido.
      // Foi assim que o primeiro rascunho errou, e o teste da janela o pegou.
      const antesDoAlvo = janela ? iNo < idxAlvo : !seenQuestion
      if (txt && !foraDaJanela) (antesDoAlvo ? before : after).push(txt)
    } else {
      byNode[node.id] = resolveLocalizedText(node.prompt, locale, dl)
      seenQuestion = true
      // Com janela, a question do TURNO é a pedida; sem ela, a primeira.
      if (janela ? iNo === idxAlvo : !firstQuestion) firstQuestion = node
      // Multi-field form (interaction: "form", approval "form padrão"): emit each
      // declared field with its own type/value/options. Otherwise the question is a
      // single scalar answer → one field keyed by output_key (survey/OTP behavior).
      // `fields` é do TURNO renderizado, então respeita a janela. Já
      // `questions[]`, `captures` e `by_node` descrevem a FORMA INTEIRA e
      // seguem completos: quem lê a forma (editor, `form_get`) precisa dela toda.
      const alvoFields = foraDaJanela ? ([] as RenderField[]) : fields
      if (node.fields && node.fields.length) {
        for (const f of node.fields) {
          // DENYLIST, nunca allowlist. Quatro coisas SAEM, cada uma com motivo:
          // `label` e `options` são reescritos aqui (i18n resolvida); `capture`
          // pertence ao mapa `captures`, não ao campo; e `value` é atribuído
          // logo abaixo, condicionalmente, porque `exactOptionalPropertyTypes`
          // recusa um `value: undefined` explícito.
          //
          // Todo o resto — `validation` inclusive, e o próximo campo que o
          // schema ganhar — atravessa. Enumerar o que FICA é o que fez
          // `validation` sumir daqui sem ninguém notar.
          const { label: _lbl, options: _opts, capture: _cap, value: _val, ...restoDoCampo } = f
          const rf: RenderField = {
            ...restoDoCampo,
            label:    resolveLocalizedText(f.label, locale, dl),
            type:     f.type,
            required: f.required ?? false,
            // Verbatim: com a união (T2), `=== true` faria `masked: "cpf"` virar
            // `false` e o campo sair DESMASCARADO — fail-open silencioso.
            masked:   f.masked ?? false,
          }
          if (f.value !== undefined) rf.value = f.value
          if (f.options && f.options.length) {
            rf.options = f.options.map(o => ({
              id:    o.value ?? o.id,
              label: resolveLocalizedText(o.label, locale, dl),
            }))
          }
          alvoFields.push(rf)
        }
      } else {
        alvoFields.push({
          id:       node.output_key,
          label:    resolveLocalizedText(node.prompt, locale, dl),
          type:     node.interaction === "text" ? "text" : "choice",
          required: true,
          // A pergunta escalar aparece nas DUAS vistas (`render.validation` no
          // topo e um campo aqui). Carregar nas duas é de propósito: quem
          // renderiza a vista de formulário não deveria precisar saber que
          // existe uma vista single-turn ao lado para descobrir a regra.
          ...(node.validation ? { validation: node.validation } : {}),
          masked:   node.masked ?? false,   // verbatim — ver acima
        })
      }
      questions.push({
        id:          node.id,
        prompt:      resolveLocalizedText(node.prompt, locale, dl),
        interaction: node.interaction,
        options:     mapOptions(node.options, locale, dl),
        output_key:  node.output_key,
        capture:     node.capture ?? {},
        visibility:  node.visibility ?? "all",
        validation:  node.validation,
        retry:       flattenRetry(node, locale, dl),
        ask_when:    node.ask_when,
      })
      captures[node.output_key] = node.capture ?? {}
    }
  }

  const q       = firstQuestion
  const qPrompt = q ? resolveLocalizedText(q.prompt, locale, dl) : ""
  // Fold leading statements into the single-question prompt (§17.4 native render).
  const prompt  = before.length ? `${before.join("\n\n")}\n\n${qPrompt}` : qPrompt
  const options: RenderOption[] = mapOptions(q?.options, locale, dl)

  return {
    interaction: q?.interaction ?? "text",
    prompt,
    options,
    output_key:  q?.output_key ?? "value",
    visibility:  q?.visibility ?? "all",
    validation:  q?.validation,
    retry:       q ? flattenRetry(q, locale, dl) : undefined,
    // §21 — the question's timeout (form JSON is raw-cast, not Zod-parsed, so the
    // schema default isn't applied here → fall back to 300, matching the schema).
    timeout_s:   (q && typeof q.timeout_s === "number") ? q.timeout_s : 300,
    questions,
    // O `||` de antes CURTO-CIRCUITAVA: havendo statement de abertura, o prompt da
    // pergunta era descartado — e as TRÊS formas `form` do repositório têm as duas
    // coisas, logo os três prompts ("Preencha os dados da solicitação:", "Dados da
    // aprovação", "Análise de crédito") nunca chegavam a ninguém. Ninguém notou
    // porque o statement sozinho faz sentido: valor plausível, de novo.
    //
    // ⚠️ Isto MUDA o texto de três formas publicadas — mudança decidida pelo dono
    // em 2026-09-04, ao construir o editor JSON (que torna a omissão visível: o
    // autor digita o prompt e espera vê-lo).
    menu_prompt: [...before, qPrompt].filter(Boolean).join("\n\n"),
    fields,
    statement_after: after.join("\n\n"),
    captures,
    by_node: byNode,
    options_tree: temArvore(options) || questions.some(x => temArvore(x.options)),
  }
}

/**
 * Ids de nó repetidos dentro de uma forma.
 *
 * Um mapa `node_id → texto` é AMBÍGUO quando dois nós compartilham o id: o segundo
 * sobrescreve o primeiro e tudo que só existia no primeiro **deixa de existir, sem
 * erro**. É o mesmo defeito que a chave duplicada num arquivo de locale produziu
 * (o parser fica com a última, e a tela mostra a chave crua) — e aqui seria pior,
 * porque a referência não resolvida vira string VAZIA e o cliente recebe um aviso
 * em branco, que é um valor plausível.
 *
 * Medido em 2026-09-03: **zero** duplicatas nas 10 formas semeadas. Fechar a classe
 * agora custa nada e remove a possibilidade, em vez de exigir vigilância.
 */
export function duplicateNodeIds(form: DialogForm): string[] {
  const vistos = new Set<string>()
  const dup    = new Set<string>()
  for (const n of form.nodes) {
    if (vistos.has(n.id)) dup.add(n.id)
    vistos.add(n.id)
  }
  return [...dup].sort()
}

// ─── Veredicto estrutural (dry-run do editor) ─────────────────────────────────

/**
 * A forma como o AUTOR a escreve: sem os três campos que o STORE é dono
 * (`tenant_id` vem do header, `created_at`/`updated_at` do banco). Validar um
 * rascunho contra o schema completo reprovaria toda forma nova por falta de
 * carimbo que o autor não tem como pôr — um validador que reprova o caso normal
 * é pior que validador nenhum, porque ensina a ignorá-lo.
 */
export const DialogFormDraftSchema = DialogFormSchema.omit({
  tenant_id:  true,
  created_at: true,
  updated_at: true,
})

export interface DialogFormIssue {
  /** Endereço no documento, no formato do zod: `nodes.1.fields.0.type`. */
  path:    string
  message: string
  code:
    | "schema" | "duplicate_node_id" | "ask_when_forward_ref" | "return_ref_unknown"
    | "option_duplicate_sibling_id" | "option_nesting_not_allowed"
    | "option_depth" | "option_empty_folder"
    | "option_description_too_long" | "option_examples_on_folder" | "option_examples_limit"
}

export interface DialogFormVerdict {
  valid:  boolean
  errors: DialogFormIssue[]
  /** O bloco que o `form_get` entregaria — só quando válido. */
  render: DialogRender | null
}

/**
 * O veredicto que o editor pergunta antes de salvar. Roda as MESMAS três guardas
 * que já existiam espalhadas — schema (zod), id de nó repetido (`form_get` recusa
 * alto por causa do `by_node`) e forward-ref de `ask_when` (a tela já checava à
 * mão no `save()`).
 *
 * ⚠️ **NÃO cobre o conflito `format` × `masked`** (§D8), e a razão é mecânica, não
 * de gosto: aquela regra precisa do CATÁLOGO de formatos (config-api) para
 * resolver `from_masked_type`, que uma função pura não tem. Ela continua no
 * publish da dialog-api, e a tela DIZ que o dry-run não a cobre — validador que
 * insinua completude é como se compra o "editor disse que estava bom e o save
 * recusou".
 */
/**
 * Ponteiros de continuação (`on_return`) que não nomeiam uma question existente.
 *
 * D2 do `adr-tree-return-continuation.md`. Irmã de `duplicateNodeIds` e de
 * `askWhenForwardRefErrors`, e existe pela mesma razão que elas: **referência
 * declarada sem mecanismo é promessa**. Um `on_return` quebrado não explode —
 * o chamador renderizaria o turno padrão, ou nada, e o cliente veria a árvore se
 * comportar como se a folha não continuasse. Valor plausível outra vez.
 *
 * ⚠️ Aponta para QUESTION, nunca para statement: statement não é ponto de
 * retomada (não coleta resposta), e apontar para um deles produziria um turno
 * que fala e não escuta — com o chamador suspenso esperando um retorno.
 */
export function returnRefErrors(form: DialogForm): Array<{ path: string; option_id: string; target: string }> {
  const questionIds = new Set(
    form.nodes.filter(n => n.kind === "question").map(n => n.id),
  )
  const out: Array<{ path: string; option_id: string; target: string }> = []

  const anda = (
    opts: ReadonlyArray<{ id: string; on_return?: string; options?: unknown }> | undefined,
    base: string,
  ): void => {
    ;(opts ?? []).forEach((o, i) => {
      const aqui = `${base}.${i}`
      if (o.on_return && !questionIds.has(o.on_return)) {
        out.push({ path: `${aqui}.on_return`, option_id: o.id, target: o.on_return })
      }
      if (Array.isArray(o.options)) {
        anda(o.options as Parameters<typeof anda>[0], `${aqui}.options`)
      }
    })
  }

  form.nodes.forEach((node, i) => {
    if (node.kind !== "question") return
    anda(node.options, `nodes.${i}.options`)
  })
  return out
}

export function validateDialogForm(doc: unknown, locale?: string): DialogFormVerdict {
  const errors: DialogFormIssue[] = []

  const parsed = DialogFormDraftSchema.safeParse(doc)
  if (!parsed.success) {
    for (const issue of parsed.error.errors) {
      errors.push({ path: issue.path.join("."), message: issue.message, code: "schema" })
    }
    return { valid: false, errors, render: null }
  }

  const form = parsed.data as unknown as DialogForm

  for (const id of duplicateNodeIds(form)) {
    errors.push({
      path:    "nodes",
      message: `id de nó repetido: '${id}' — render.by_node ficaria ambíguo e a referência ao nó perdido viraria texto vazio`,
      code:    "duplicate_node_id",
    })
  }

  // Árvore de opções (F1 do `adr-dialog-tree-options`). O aninhamento é
  // permitido pela INTERAÇÃO da pergunta, e recusado no campo: `DialogField` não
  // tem interação, logo não há superfície que saiba desenhar sua subárvore.
  form.nodes.forEach((node, i) => {
    if (node.kind !== "question") return
    const permite = node.interaction === "list" || node.interaction === "checklist"
    for (const issue of optionTreeIssues(node.options, {
      allowNesting: permite,
      base: `nodes.${i}.options`,
    })) {
      errors.push({ path: issue.path, message: issue.message, code: issue.code })
    }
    ;(node.fields ?? []).forEach((f, j) => {
      for (const issue of optionTreeIssues(f.options, {
        allowNesting: false,
        base: `nodes.${i}.fields.${j}.options`,
      })) {
        errors.push({ path: issue.path, message: issue.message, code: issue.code })
      }
    })
  })

  for (const { path, option_id, target } of returnRefErrors(form)) {
    errors.push({
      path,
      message: `on_return da opção '${option_id}' aponta para '${target}', que não é uma question deste form — a folha diria que continua e não continuaria`,
      code:    "return_ref_unknown",
    })
  }

  for (const { node_id, field } of askWhenForwardRefErrors(form)) {
    errors.push({
      path:    `nodes.${form.nodes.findIndex(n => n.id === node_id)}.ask_when.field`,
      message: `'${field}' não é resposta de uma pergunta ANTERIOR (guarda referencia para a frente ou para chave inexistente)`,
      code:    "ask_when_forward_ref",
    })
  }

  if (errors.length) return { valid: false, errors, render: null }
  return { valid: true, errors: [], render: buildRender(form, locale) }
}
