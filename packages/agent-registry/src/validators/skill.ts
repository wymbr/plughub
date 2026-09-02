/**
 * validators/skill.ts
 * Validação Zod dos payloads de skill + validações cross-step.
 *
 * Cross-field validators:
 *   - validateMaskedBlock  — reason step inside begin/end_transaction block
 */

import { z }           from "zod"
import { SkillSchema, typeMasksSomething } from "@plughub/schemas"
import type { FlowStep, SkillFlow, DataType } from "@plughub/schemas"
import {
  buildContextTagIndex, collectContextTagWrites, resolveContextTag,
  type ContextMap, type ContextTagIndex,
} from "@plughub/schemas"

export const CreateSkillSchema = SkillSchema

// SkillSchema is ZodEffects (has .refine). Access the inner ZodObject for partial operations.
const _SkillBase = (SkillSchema as unknown as { _def: { schema: z.ZodObject<z.ZodRawShape> } })._def.schema
export const UpdateSkillSchema = _SkillBase.partial().omit({ skill_id: true })

// ─────────────────────────────────────────────
// validateMaskedBlock
// ─────────────────────────────────────────────

/**
 * Validates that no `reason` step exists inside a begin_transaction / end_transaction block.
 *
 * A `reason` step inside a masked block is a design error because it sends user data to an
 * external LLM, which could inadvertently expose sensitive values captured by masked fields.
 * Spec: docs/guias/masked-input.md — "reason step dentro de bloco masked é erro de design".
 *
 * Engine behaviour (from engine.ts):
 *   begin_transaction returns "__transaction_begin__" → engine advances to the
 *   NEXT STEP IN THE ARRAY (position N+1). All subsequent steps reachable via
 *   on_success chains until end_transaction are inside the block.
 *
 * Algorithm:
 *   For each begin_transaction step at array position N:
 *     - Start BFS from position N+1 (the first step inside the block)
 *     - For each visited step, extract "success-edge" step IDs (on_success, choice branches, etc.)
 *     - Stop propagating at end_transaction (block closed) or on_failure exits
 *     - If a reason step is found inside the block, emit an error
 *
 * @returns Array of error strings — empty array when the flow is valid.
 */
export function validateMaskedBlock(flow: SkillFlow): string[] {
  const errors: string[] = []
  const steps = flow.steps
  if (!steps || steps.length === 0) return errors

  // ── Step map and position map ──────────────────────────────────────────────
  const stepById  = new Map<string, FlowStep>()
  const stepIndex = new Map<string, number>()  // stepId → position in array
  for (let i = 0; i < steps.length; i++) {
    stepById.set(steps[i]!.id, steps[i]!)
    stepIndex.set(steps[i]!.id, i)
  }

  // ── Success-edge extractor ─────────────────────────────────────────────────
  // Returns all step IDs that `step` can transition to via "happy path" edges.
  // Excludes on_failure / on_disconnect / on_timeout (exit paths).
  function successors(step: FlowStep): string[] {
    const ids: string[] = []

    if (step.type === "begin_transaction") {
      // begin_transaction has no on_success — engine uses position N+1 (handled by caller)
      return []
    }

    // Generic on_success present on most step types
    const s = step as FlowStep & { on_success?: string }
    if (typeof s.on_success === "string" && stepById.has(s.on_success)) {
      ids.push(s.on_success)
    }

    // choice step: all conditional branches + default
    if (step.type === "choice") {
      for (const cond of step.conditions) {
        if (cond.next && stepById.has(cond.next)) ids.push(cond.next)
      }
      if (step.default && stepById.has(step.default)) ids.push(step.default)
    }

    // suspend step: on_resume.next
    if (step.type === "suspend") {
      const on_resume = (step as { on_resume?: { next?: string } }).on_resume
      if (on_resume?.next && stepById.has(on_resume.next)) ids.push(on_resume.next)
    }

    // collect step: on_response
    if (step.type === "collect") {
      const on_response = (step as { on_response?: { next?: string } }).on_response
      if (on_response?.next && stepById.has(on_response.next)) ids.push(on_response.next)
    }

    return [...new Set(ids)]
  }

  // ── BFS from each begin_transaction ───────────────────────────────────────
  for (let i = 0; i < steps.length; i++) {
    const startStep = steps[i]!
    if (startStep.type !== "begin_transaction") continue

    // The first step inside the block is the one at position i+1
    const firstInBlock = steps[i + 1]
    if (!firstInBlock) continue

    const visited = new Set<string>()
    const queue: string[] = [firstInBlock.id]

    while (queue.length > 0) {
      const stepId = queue.shift()!
      if (visited.has(stepId)) continue
      visited.add(stepId)

      const step = stepById.get(stepId)
      if (!step) continue

      // end_transaction closes the block — stop this path
      if (step.type === "end_transaction") continue

      // Validate: reason step inside masked block is forbidden
      if (step.type === "reason") {
        errors.push(
          `Step "${stepId}" (reason) is inside masked transaction block ` +
          `started by "${startStep.id}". reason steps must not appear inside ` +
          `begin_transaction / end_transaction blocks — they send data to an external LLM ` +
          `and could expose sensitive values captured by masked fields. ` +
          `Move the reason step before begin_transaction or after end_transaction.`
        )
        // Don't propagate further from a reason step — one error per step is enough
        continue
      }

      // Propagate through success edges
      for (const next of successors(step)) {
        if (!visited.has(next)) queue.push(next)
      }
    }
  }

  return errors
}

// ─────────────────────────────────────────────────────────────────────────────
// T5 — portão de DEPLOY do `masked` tipado (ADR adr-masked-typed-declaration, D3)
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Colhe as declarações TIPADAS de `masked` no flow — só as strings.
 *
 * `true`/`false` não entram: `true` resolve para `opaque`, que é um tipo real do
 * catálogo e não precisa de conferência; `false` não declara nada.
 *
 * Exportada porque o gate ancora nela: um predicado com nome é conferível, uma
 * condição inline no meio da rota não é.
 */
export function collectMaskedTypeRefs(flow: SkillFlow): Array<{ step: string; field: string; type: string }> {
  const out: Array<{ step: string; field: string; type: string }> = []
  const typed = (m: unknown): string | null =>
    (typeof m === "string" && m.trim().length > 0) ? m.trim() : null

  for (const step of flow.steps ?? []) {
    const s = step as FlowStep & {
      masked?: unknown
      fields?: Array<{ id?: string; masked?: unknown }>
      output_as?: string
    }
    const stepType = typed(s.masked)
    if (stepType) out.push({ step: step.id, field: s.output_as ?? step.id, type: stepType })
    for (const f of s.fields ?? []) {
      const ft = typed(f?.masked)
      if (ft) out.push({ step: step.id, field: f?.id ?? "?", type: ft })
    }
  }
  return out
}

/**
 * Confere as referências tipadas contra o catálogo VIVO do tenant.
 *
 * **Fail-closed, e o acoplamento é ESCOPADO**: um flow sem nenhuma declaração
 * tipada não busca o catálogo, logo não depende do config-api para ser salvo.
 * Só quem declara um tipo paga a dependência — e paga porque, sem conferir, o
 * deploy grava um id que ninguém resolve, e o defeito só aparece meses depois,
 * na transcrição, como um `masked_types` que não casa com tipo nenhum.
 *
 * Catálogo inalcançável ⇒ RECUSA. É a postura oposta à de leitura de relatório,
 * e deliberada: aqui não se pode verificar, e mascaramento é a política em que
 * "não sei" nunca pode virar "pode passar".
 *
 * Devolve lista de mensagens; vazia = aprovado.
 */
export async function validateMaskedTypeRefs(
  flow: SkillFlow,
  opts: { tenantId: string; configApiUrl: string; fetchImpl?: typeof fetch },
): Promise<string[]> {
  const refs = collectMaskedTypeRefs(flow)
  if (refs.length === 0) return []            // sem tipado ⇒ sem dependência

  const doFetch = opts.fetchImpl ?? fetch
  let ids:   Set<string>
  let inert: Set<string> = new Set()
  try {
    const url  = `${opts.configApiUrl}/config/masking?tenant_id=${encodeURIComponent(opts.tenantId)}`
    const resp = await doFetch(url)
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
    const body = await resp.json() as { entries?: Record<string, unknown> }
    const raw  = body?.entries?.["types"]
    const cat  = (raw && typeof raw === "object" && "value" in (raw as object))
      ? (raw as { value: unknown }).value
      : raw
    const types = (cat as { types?: DataType[] } | undefined)?.types
    if (!Array.isArray(types) || types.length === 0) throw new Error("catálogo vazio ou ausente")
    ids = new Set(types.map(t => String(t?.id ?? "")).filter(Boolean))
    // V3 — o catálogo passou a poder conter tipo que NÃO mascara (`texto`, para o
    // mapa do ContextStore). Conferir só a EXISTÊNCIA do id deixaria passar
    // `masked: "texto"`: campo declarado mascarado, renderizado em claro, com selo
    // de conformidade. O predicado é derivado do tipo, nunca uma lista de exceção.
    inert = new Set(types.filter(t => t && !typeMasksSomething(t)).map(t => String(t.id ?? "")).filter(Boolean))
  } catch (err) {
    // Degradação NUNCA silenciosa, e aqui nem sequer degrada: recusa NOMEANDO.
    return [
      `não foi possível conferir os tipos de masking contra o catálogo do tenant ` +
      `(${String(err)}). ${refs.length} referência(s) tipada(s) no flow — o deploy é ` +
      `recusado porque "não sei" não pode virar "pode passar" em mascaramento.`,
    ]
  }

  const unknown = refs
    .filter(r => !ids.has(r.type))
    .map(r =>
      `step "${r.step}", campo "${r.field}": masked: "${r.type}" não existe no catálogo ` +
      `masking.types do tenant. Tipos declarados: ${[...ids].sort().join(", ")}.`,
    )

  // Recusa NOMEANDO o motivo, que é diferente do de cima: o tipo existe, e é por
  // isso que a mensagem tem de dizer que ele não mascara — senão o autor procura
  // um erro de digitação num id que está certo.
  const doesNotMask = refs
    .filter(r => ids.has(r.type) && inert.has(r.type))
    .map(r =>
      `step "${r.step}", campo "${r.field}": masked: "${r.type}" existe no catálogo mas ` +
      `NÃO mascara para papel nenhum (\`mascara.by_role\` vazio ou todo "plain"). ` +
      `Declarar um campo como mascarado com um tipo inerte o exibiria em claro — ` +
      `é o tipo do MAPA do ContextStore, não de declaração \`masked\`.`,
    )

  return [...unknown, ...doesNotMask]
}

/**
 * V4 — o PORTÃO DE CADASTRO do ContextStore (ADR allowlist, D9.1).
 *
 * Toda tag que o flow ESCREVE tem de estar no mapa do tenant. O que não está não passa
 * aqui; no runtime nada é rejeitado (grava, resolve restritivo e LOGA).
 *
 * ── ONDE ele mora, e por que não é no promote ───────────────────────────────
 *
 * `x-skill-publish` virou no-op em 2026-07-13: há UMA definição (`flow`), e produção roda
 * o snapshot do slot (set-next → promote). "Publish" no sentido do ADR poderia então ser
 * o promote. Está aqui, no `PUT`, de propósito: é onde o AUTOR recebe a resposta. Gatear
 * no promote deixaria o autor salvar e descobrir no deploy — mais tarde e pior. É também
 * o ponto do vizinho (`validateMaskedTypeRefs`, o portão da T5).
 *
 * ── A POSTURA É OPOSTA À DO VIZINHO, e isso é medição ───────────────────────
 *
 * `validateMaskedTypeRefs` RECUSA quando não consegue conferir — *"mascaramento é a
 * política em que 'não sei' nunca pode virar 'pode passar'"*. Aqui é o contrário, por
 * duas diferenças concretas:
 *
 *   **(1) O acoplamento é universal, não escopado.** Lá, só quem declara um tipo paga a
 *   dependência do config-api, e quase nenhum flow declara. Aqui, praticamente TODO flow
 *   escreve alguma tag — então recusar-por-não-saber poria cada `PUT` de skill, e com ele
 *   o boot inteiro do RegistrySyncer, atrás da disponibilidade do config-api. É a forma
 *   exata do defeito que a ALW-12 acabou de consertar: provisionamento que falha em
 *   silêncio derruba o runtime.
 *
 *   **(2) O runtime tem rede de segurança, e o de masking não tinha.** Uma tag que escapa
 *   daqui é gravada, CARIMBADA como `unknown`, CONTADA pela auditoria da V3 e LOGADA pelo
 *   funil (F1) — e resolve para o mais restritivo na leitura. Não é vazamento: é um campo
 *   que o operador legítimo pode não ver, e que aparece em três instrumentos.
 *
 * Então: catálogo inalcançável ⇒ **passa, gritando**. A mensagem nomeia o que deixou de
 * valer, em vez de dizer "using default" — a § Configuration já registra que foi
 * exatamente essa frase genérica que ninguém leu por meses.
 *
 * ── Nome DINÂMICO é recusado, e não é preciosismo ───────────────────────────
 *
 * A D9.2 mediu **zero** nomes interpolados em 21 escritas, e o modelo inteiro vive desse
 * zero: se um nome só existe em runtime, a análise estática não fecha e o portão volta a
 * ser observação (*"rodar tráfego, esperar aparecer, repetir até secar"*). O primeiro que
 * aparecer tem de ser uma decisão consciente, não um silêncio.
 */
/**
 * Prefixo que marca a linha de tag ARQUIVADA dentro do array devolvido por
 * `validateContextTagRegistration`.
 *
 * Aquela função devolve `string[]` (contrato herdado dos vizinhos `validateMasked*`),
 * e quem a chama precisa distinguir as DUAS famílias para dar `code` diferente — o
 * editor escolhe a afordância pelo código, e *"cadastre este campo"* é o conselho
 * ERRADO para um campo que existe e foi aposentado: cadastrá-lo de novo com o mesmo
 * nome desfaria, em silêncio, a decisão de quem arquivou.
 *
 * Marcador no texto e não um tipo de retorno novo: mudar a assinatura obrigaria a
 * mudar os três validadores irmãos por um fato que só um deles tem.
 */
export const ARCHIVED_PREFIX = "[arquivada] "

export async function validateContextTagRegistration(
  flow: SkillFlow,
  opts: { tenantId: string; configApiUrl: string; fetchImpl?: typeof fetch },
): Promise<string[]> {
  const writes = collectContextTagWrites(flow)
  if (writes.length === 0) return []          // flow que não escreve ctx ⇒ sem dependência

  // Dinâmico não depende do mapa: recusa antes de qualquer I/O.
  const dinamicos = writes.filter(w => w.dynamic)
  if (dinamicos.length > 0) {
    return dinamicos.map(w =>
      `step "${w.step ?? "?"}" (${w.surface}): a tag "${w.tag}" tem nome INTERPOLADO. ` +
      `O cadastro do ContextStore é conferido por análise estática, e um nome que só ` +
      `existe em runtime não pode ser conferido — a premissa medida da D9.2 é que não há ` +
      `nenhum (0 em 21 escritas). Use um nome literal, ou traga a decisão para o ADR.`,
    )
  }

  let index: ContextTagIndex
  const arquivadasNoMapa = new Set<string>()
  try {
    const url  = `${opts.configApiUrl}/config/masking?tenant_id=${encodeURIComponent(opts.tenantId)}`
    const resp = await (opts.fetchImpl ?? fetch)(url)
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
    const body = await resp.json() as { entries?: Record<string, unknown> }
    const raw  = body?.entries?.["context_map"]
    const mapa = (typeof raw === "string" ? JSON.parse(raw) : raw) as ContextMap | undefined
    if (!mapa?.contexto) throw new Error("resposta sem `entries.context_map.contexto`")
    index = buildContextTagIndex(mapa)
    // Do MAPA, não do índice — ver o comentário de `arquivado` em `context-map.ts`.
    for (const [escopo, doms] of Object.entries(mapa.contexto)) {
      for (const [dom, campos] of Object.entries(doms)) {
        for (const [campo, folha] of Object.entries(campos)) {
          if (folha?.arquivado) arquivadasNoMapa.add(`${escopo}.${dom}.${campo}`)
        }
      }
    }
  } catch (err) {
    // PASSA, GRITANDO — ver o cabeçalho. A frase diz o que deixou de valer, não que
    // "usou o default": um aviso genérico é um aviso que ninguém lê.
    console.warn(
      `[ctx-gate] tenant=${opts.tenantId}: NÃO foi possível conferir o cadastro do ` +
      `ContextStore contra o config-api (${String(err)}). ${writes.length} escrita(s) de ` +
      `tag neste flow passaram SEM conferência. Deixa de valer: a garantia de que toda ` +
      `tag escrita está registrada. Segue valendo: o carimbo de origem, o contador de ` +
      `\`unknown\` da auditoria e o aviso do funil em runtime.`,
    )
    return []
  }

  // ── ARQUIVADAS — declaradas, mas proibidas para escrita NOVA (E1) ─────────
  //
  // Separado das não cadastradas de propósito: a mensagem tem de dizer coisas
  // diferentes. "Não existe, cadastre" manda o autor criar; "existe mas foi
  // aposentada" manda escolher outra — e criar de novo com o mesmo nome seria
  // desfazer a decisão de quem arquivou, em silêncio.
  const arquivadas = writes.filter(w => {
    const r = resolveContextTag(w.tag, index)
    return r.origin !== "unknown" && arquivadasNoMapa.has(r.canonical)
  })

  const naoCadastradas = writes.filter(w => resolveContextTag(w.tag, index).origin === "unknown")
  if (naoCadastradas.length === 0 && arquivadas.length === 0) return []

  const msgsArquivadas = arquivadas.map(w => ARCHIVED_PREFIX + (() => {
    const canon = resolveContextTag(w.tag, index).canonical
    const via   = canon === w.tag ? "" : ` (alias de "${canon}")`
    return (
      `step "${w.step ?? "?"}" (${w.surface}): a tag "${w.tag}"${via} está ARQUIVADA no ` +
      `mapa do ContextStore. Ela continua declarada — o histórico já gravado segue ` +
      `mascarado — mas escrita NOVA não é aceita. Use outro campo, ou desarquive em ` +
      `/config/context-map se a aposentadoria foi engano.`
    )
  })())
  if (naoCadastradas.length === 0) return msgsArquivadas

  // Uma linha por tag, e ela diz EXATAMENTE o que registrar — é a metade que faltava da
  // decisão #2 do ADR (*"o erro de publish precisa dizer exatamente o que registrar"*).
  // `escopo.dominio.campo` porque é essa a forma do mapa, e sem ela o autor abre a tela
  // sem saber em que caixa digitar.
  return [...msgsArquivadas, ...naoCadastradas.map(w => {
    const partes = w.tag.split(".")
    const forma  = partes.length >= 3
      ? `escopo "${partes[0]}", domínio "${partes[1]}", campo "${partes.slice(2).join(".")}"`
      : `⚠️ o nome tem ${partes.length} segmento(s); o mapa é "escopo.dominio.campo"`
    return (
      `step "${w.step ?? "?"}" (${w.surface}): a tag "${w.tag}" NÃO está cadastrada no ` +
      `mapa do ContextStore deste tenant. Registre em /config/context-map — ${forma} — ` +
      `escolhendo um tipo do catálogo. Enquanto não estiver, ela é gravada mas resolve ` +
      `para o mais RESTRITIVO na leitura, e um operador legítimo pode não ver o valor.`
    )
  })]
}

/* ─────────────────────────────────────────────────────────────────────────────
 * VERIFICADOR ÚNICO DE PAYLOAD DE SKILL — F1 do ADR do editor (D2/D3)
 * ────────────────────────────────────────────────────────────────────────────*/

export interface SkillValidationError {
  /** Código estável, para o cliente decidir afordância (ex.: link para o cadastro). */
  code:     string
  message:  string
  /** Caminho no corpo, formato YAML-ish (`steps[3].on_success`) quando conhecido. */
  path?:    string
  step_id?: string
}

export interface SkillValidationResult {
  valid:   boolean
  errors:  SkillValidationError[]
  /** O corpo já parseado, quando válido — para o `PUT` não parsear duas vezes. */
  parsed?: z.infer<typeof CreateSkillSchema>
}

/**
 * O que o `PUT /v1/skills/:id` aceita — em UMA função, chamada pelo `PUT` e pelo
 * dry-run `POST /v1/skills/validate`.
 *
 * ── Por que extrair, e o que deliberadamente NÃO mudou ──────────────────────
 *
 * D3 do `adr-skill-flow-editor-validation.md`: *"se divergirem, volta-se ao padrão de
 * duas respostas"* — o editor perguntaria a um verificador e o save rodaria outro, que é
 * a forma mais cara de um erro aparecer só no save.
 *
 * ⚠️ **Esta extração é PARIDADE, não endurecimento.** A D3 também prevê mover o
 * `validateFlow` (detecção de ciclo não-guardado, hoje só no engine) para cá — e avisa
 * que isso é *"mudança de comportamento, não refactor"*, com pré-requisito medido: rodar
 * o verificador sobre os 42 YAMLs existentes antes de ligar. **Aquilo fica de fora
 * daqui**: ligar um verificador mais estrito no mesmo commit em que se extrai o
 * verificador tornaria impossível dizer qual das duas coisas quebrou o que quebrar.
 *
 * A ordem das checagens é a do `PUT` de hoje, e importa: a lápide do `agent_role`
 * pergunta ao corpo **CRU**, porque depois do parse a chave desconhecida já sumiu e não
 * haveria o que recusar.
 */
export async function validateSkillPayload(
  raw:  unknown,
  opts: { tenantId: string; configApiUrl: string; fetchImpl?: typeof fetch },
): Promise<SkillValidationResult> {
  const errors: SkillValidationError[] = []

  // ── lápide do `agent_role` — sobre o corpo CRU ────────────────────────────
  if (raw != null && typeof raw === "object" && "agent_role" in (raw as Record<string, unknown>)) {
    return {
      valid: false,
      errors: [{
        code: "agent_role_removed",
        path: "agent_role",
        message:
          "O campo `agent_role` foi REMOVIDO em 2026-09-01. Ele tinha um consumidor só — " +
          "o gate de evaluation_context_get/evaluation_submit — e esse gate saiu por não " +
          "impedir cenário nenhum (lia o papel do participant_id do INPUT). Remova o campo " +
          "do payload: mandá-lo não declara mais nada. Ver docs/adr/adr-remove-agent-role-axis.md.",
      }],
    }
  }

  // ── forma (Zod) ───────────────────────────────────────────────────────────
  const p = CreateSkillSchema.safeParse(raw)
  if (!p.success) {
    // `safeParse` e não `parse`: o dry-run precisa DEVOLVER os erros, não lançá-los
    // para o handler global. O `PUT` mantém o mesmo desfecho porque quem responde 422
    // é a rota, com o mesmo conteúdo.
    return {
      valid: false,
      errors: p.error.issues.map(i => ({
        code:    "invalid_shape",
        path:    i.path.join("."),
        message: i.message,
      })),
    }
  }
  const body = p.data

  if (body.flow) {
    for (const m of validateMaskedBlock(body.flow)) {
      errors.push({ code: "invalid_masked_block", message: m })
    }
    if (errors.length === 0) {
      for (const m of await validateMaskedTypeRefs(body.flow, opts)) {
        errors.push({ code: "invalid_masked_type", message: m })
      }
    }
    if (errors.length === 0) {
      for (const m of await validateContextTagRegistration(body.flow, opts)) {
        // `code` estável e específico: é por ELE que o editor decide a afordância. Um
        // código genérico obrigaria o cliente a casar texto de mensagem, que é a forma
        // mais frágil de acoplamento entre camadas — e as duas famílias pedem conselhos
        // OPOSTOS: "cadastre" × "escolha outro campo, ou desarquive".
        const arq = m.startsWith(ARCHIVED_PREFIX)
        errors.push({
          code:    arq ? "archived_context_tag" : "unregistered_context_tag",
          message: arq ? m.slice(ARCHIVED_PREFIX.length) : m,
        })
      }
    }
  }

  return errors.length > 0
    ? { valid: false, errors }
    : { valid: true, errors: [], parsed: body }
}
