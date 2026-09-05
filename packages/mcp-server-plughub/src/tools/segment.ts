/**
 * tools/segment.ts — Camada E2 (wrap-up-α), sub-fatia 2.
 *
 * Tool MCP `segment_outcome_record`: grava a DISPOSIÇÃO do wrap-up destacado no
 * SEGMENTO da sessão de ORIGEM, por REFERÊNCIA (origin_session_id + segment_id) —
 * sem que o wrap-up seja fisicamente um segmento da conferência.
 *
 * Replica fielmente o par do orchestrator-bridge:
 *   _apply_wrapup_to_segment  → normaliza classificação→outcome, acumula no hash
 *                                Redis `session:{origin}:seg_signal:{seg}`
 *   _republish_segment_from_signal → lê o hash COMPLETO e re-publica
 *                                `participant_left` em `conversations.participants`
 *
 * CUIDADO (ReplacingMergeTree substitui a LINHA INTEIRA — CLAUDE.md § Postura de
 * Engenharia): o hash `seg_signal` já foi semeado com os campos ESTÁTICOS do
 * segmento (instance_id, pool_id, joined_at, duration_ms, …) por `_seed_segment_signal`
 * quando o hook on_human_end disparou. Publicamos a linha COMPLETA (estáticos do hash
 * + os dinâmicos do wrap-up). Se o hash não tem `segment_id` (nunca semeado), NÃO
 * publicamos (no-op barulhento) — publicar uma linha parcial zeraria as colunas do
 * segmento no analytics. Mesma guarda do bridge.
 *
 * Idempotente por construção (dedup do ReplacingMergeTree por segment_id).
 * Interceptado pelo McpInterceptor — auditado em mcp.audit (LGPD).
 * Publica em: conversations.participants → analytics.segments.
 */

import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js"
import { z }         from "zod"
import { randomUUID } from "crypto"
import type { RedisClient } from "../infra/redis"
import { buildAgentBusinessEvent } from "./agent-events"
import { AGENT_EVENT_CATEGORY_MAX_SEGMENTS } from "@plughub/schemas"

export interface SegmentDeps {
  redis: RedisClient
  kafka: { publish: (topic: string, payload: Record<string, unknown>) => Promise<void> }
  tenantId: string
  /** dialog-api base URL — fatia 3: a tool lê o form para saber o que capturar. */
  dialogApiUrl?: string
}

const TOPIC_PARTICIPANTS = "conversations.participants"
const TOPIC_AGENT_EVENTS = "agent.events"

// ─── Fatia 3 — captura dirigida pelo FORMULÁRIO ───────────────────────────────
//
// Antes: a tool tinha contrato fixo de 4 campos e o SKILL mapeava
// `answers.classificacao`/`resumo`/`proximos_passos` um a um. Cada pergunta nova no
// editor exigia editar o skill + `set-next` + `promote` — ou seja, o formulário não
// dirigia nada, que era o ponto (§D3).
//
// Agora a tool recebe `answers` inteiro + `dialog_form_id`, busca o form publicado e
// roteia CADA resposta pela declaração dela:
//
//   capture.kind = "scored"   → agent.events, categoria `{pool}.{skill}.{metric}`,
//                               value = resposta numérica  (avg_value É a taxa)
//   capture.kind = "nominal"  → agent.events, categoria `…{metric}.{opção}`, value 1
//                               (multi-select ⇒ N eventos)
//   sem capture, chave conhecida → prosa nas colunas do segmento
//   sem capture, chave desconhecida → IGNORADA, com log
//
// O núcleo (`classificacao` → outcome) NÃO é capture: é a disposição do segmento,
// contrato do wrap-up, e some do Arc 12 de propósito — `outcome` já vive em
// `segments` e duplicá-lo criaria duas fontes para a mesma pergunta (§D6).

/** Chaves de resposta que a tool trata como núcleo do wrap-up (não vão ao Arc 12). */
const CORE_ANSWER_KEYS = new Set(["classificacao", "resumo", "proximos_passos", "escalation_reason"])

type FormQuestion = {
  id?: string
  output_key?: string
  capture?: { metric?: string; kind?: string; value?: number | string }
  options?: Array<{ id?: string; value?: number | string; capture?: { value?: number | string } }>
}

/**
 * Busca o DialogForm — mesmo caminho que `survey_record` usa (§D3).
 *
 * **Com `version`, busca AQUELA versão; sem, a última publicada.** É a metade de
 * leitura do pin (S1 do `adr-deploy-time-content-snapshot`, D14 da árvore): o
 * servidor gravou no delegate qual versão o atendente veria, e aqui ela é honrada.
 * Sem o pin, esta função se comporta exatamente como antes — por isso a ausência
 * degrada sem quebrar, e é a razão de o pin poder ser omitido lá atrás.
 *
 * ⚠️ A versão vem do CONTEXTO (`@ctx.core.workflow.dialog_form_version`, escrito
 * pelo servidor), nunca do payload de quem submete: senão o cliente escolheria
 * qual documento descreve a própria resposta.
 */
async function fetchPublishedForm(
  dialogApiUrl: string, tenantId: string, formId: string, version?: number,
): Promise<{ nodes: FormQuestion[] } | null> {
  const query = version != null
    ? `version=${encodeURIComponent(String(version))}`
    : "status=published"
  try {
    const resp = await fetch(
      `${dialogApiUrl}/v1/dialog/forms/${encodeURIComponent(formId)}?${query}`,
      { headers: { "X-Tenant-ID": tenantId } },
    )
    if (!resp.ok) {
      // A versão entra no log: um 404 com pin significa "a versão que o atendente
      // viu sumiu do store", que é diagnóstico diferente de "o form não existe".
      console.warn(
        "[segment_outcome_record] dialog-api %s ao buscar form=%s (%s)",
        resp.status, formId, query,
      )
      return null
    }
    return (await resp.json()) as { nodes: FormQuestion[] }
  } catch (err) {
    // Degradação NUNCA silenciosa: sem o form não há captura Arc 12, e o motivo é logado.
    // A prosa e o outcome seguem gravados — perder a captura não pode perder a disposição.
    console.warn("[segment_outcome_record] falha ao buscar form=%s: %s", formId, String(err))
    return null
  }
}

/**
 * sanitizeCategoryPath — normaliza a resposta em segmentos de categoria,
 * **preservando o `.` como separador** (F4 do `adr-dialog-tree-options`).
 *
 * O sanitizador anterior era `replace(/[^a-z0-9_]+/g, "_")` sobre a resposta
 * inteira, e o ponto caía nessa classe: `financeiro.cobranca.indevida` virava
 * `financeiro_cobranca_indevida` — **um** segmento. Três consequências, todas
 * mudas:
 *
 *   · a hierarquia morria no emissor, então o teto de segmentos nunca era
 *     alcançado e a subida dele não teria efeito nenhum;
 *   · `startsWith(category, "…motivo.financeiro.")` — o recorte da D10 — não
 *     casaria com nada, porque não havia ponto onde procurar;
 *   · a pasta `financeiro.cobranca` e uma folha chamada `financeiro_cobranca`
 *     colidiriam na MESMA categoria, duas coisas numa série só.
 *
 * Cada segmento continua sendo saneado como antes; o que muda é sanear POR
 * segmento em vez de sobre a string toda.
 */
function sanitizeCategoryPath(v: string): string {
  return v
    .trim()
    .toLowerCase()
    .split(".")
    .map((seg) => seg.replace(/[^a-z0-9_]+/g, "_"))
    .filter((seg) => seg.length > 0)
    .join(".")
}

/**
 * Deriva os eventos Arc 12 das respostas, lendo o form. Devolve [] quando não há
 * form, o que é diferente de "o form não tinha captura" — o chamador loga os dois.
 */
export function deriveAgentEvents(
  form:      { nodes: FormQuestion[] } | null,
  answers:   Record<string, unknown>,
  ctx:       { poolId: string; skillId: string },
): Array<{ category: string; value: number }> {
  if (!form?.nodes?.length) return []
  const out: Array<{ category: string; value: number }> = []
  for (const q of form.nodes) {
    const cap = q.capture
    if (!cap?.kind || !cap.metric) continue
    const key = q.output_key || q.id || ""
    if (!key || !(key in answers)) continue
    const raw = answers[key]
    if (raw === undefined || raw === null || raw === "") continue
    const base = `${ctx.poolId}.${ctx.skillId}.${cap.metric}`

    if (cap.kind === "scored") {
      // A resposta pode ser o `value` da opção (botão "4") ou um escalar numérico.
      const n = Number(
        q.options?.find((o) => String(o.value ?? o.id) === String(raw))?.capture?.value ?? raw,
      )
      if (!Number.isFinite(n)) {
        console.warn("[segment_outcome_record] scored não-numérico: %s=%j (ignorado)", key, raw)
        continue
      }
      out.push({ category: base, value: n })
      continue
    }

    // nominal — a resposta VIRA a folha. Multi-select chega como array ⇒ N eventos.
    // A folha sai do valor da OPÇÃO (lista controlada), nunca de texto livre (§D3).
    for (const item of Array.isArray(raw) ? raw : [raw]) {
      const leaf = sanitizeCategoryPath(String(item))
      if (!leaf) continue
      const category = `${base}.${leaf}`
      if (category.split(".").length > AGENT_EVENT_CATEGORY_MAX_SEGMENTS) {
        // Emitir aqui produziria um evento que o schema do Arc 12 REJEITA depois,
        // longe daqui — recusar na origem, nomeando, é o que transforma isso em
        // erro de autoria em vez de buraco na série.
        console.warn(
          "[segment_outcome_record] caminho fundo demais para a categoria do Arc 12: %s (%d segmentos, teto %d) — evento NÃO emitido",
          category, category.split(".").length, AGENT_EVENT_CATEGORY_MAX_SEGMENTS,
        )
        continue
      }
      out.push({ category, value: 1 })
    }
  }
  return out
}

// Mesmo mapa do bridge (_WRAPUP_OUTCOME_MAP): classificação CRUA → outcome canônico.
const WRAPUP_OUTCOME_MAP: Record<string, string> = {
  resolvido: "resolved",
  pendente:  "suspended",
  escalado:  "escalated",
  cancelado: "abandoned",
}

const segSignalKey = (sessionId: string, segmentId: string) =>
  `session:${sessionId}:seg_signal:${segmentId}`

/**
 * Converte o pin de versão para inteiro — ou `undefined`, NUNCA um palpite.
 *
 * O valor chega do ContextStore, onde tudo é string. `Number("")` é `0` e
 * `Number("1abc")` é `NaN`: os dois passariam por uma coerção descuidada, e um `0`
 * viraria `?version=0` (404) enquanto um `NaN` viraria `?version=NaN`. Ambos
 * quebrariam a captura Arc 12 num caminho que a §D3 manda degradar sem perder a
 * disposição — mas quebrariam por motivo INVENTADO aqui, não por defeito real.
 *
 * `undefined` = sem pin = última publicada, que é o comportamento anterior ao
 * mecanismo. É a degradação certa, e ela é LOGADA no chamador.
 */
function parsePin(raw: string | number | undefined): number | undefined {
  if (raw == null || raw === "") return undefined
  const n = typeof raw === "number" ? raw : Number(raw.trim())
  if (!Number.isInteger(n) || n < 1) return undefined
  return n
}

function mcpOk(data: unknown) {
  return { content: [{ type: "text" as const, text: JSON.stringify(data) }] }
}
function mcpError(code: string, message: string) {
  return { isError: true, content: [{ type: "text" as const, text: JSON.stringify({ error: code, message }) }] }
}

export function registerSegmentTools(server: McpServer, deps: SegmentDeps): void {
  const { redis, kafka, tenantId: defaultTenant, dialogApiUrl } = deps

  server.tool(
    "segment_outcome_record",
    "Grava a disposição do WRAP-UP destacado no SEGMENTO da sessão de origem, por " +
    "referência (origin_session_id + segment_id). Normaliza a classificação em outcome, " +
    "acumula no seg_signal e re-publica participant_left para o analytics. Usado pelo " +
    "workflow de wrap-up no on_resume (skill_wrapup_detached_v1).",
    {
      origin_session_id: z.string().describe("Sessão de ORIGEM (o contato que fechou) — chaveia o seg_signal"),
      segment_id:        z.string().describe("Segmento humano a atribuir (@ctx.core.survey.segment_id)"),
      // Fatia 3 — o caminho preferido: respostas cruas + o form que as descreve.
      answers:           z.record(z.any()).optional().describe("Respostas do DialogForm ($.pipeline_state.coletar.answers). Preferido — dispensa mapear campo a campo"),
      dialog_form_id:    z.string().optional().describe("Form respondido (@ctx.core.workflow.dialog_form_id) — dirige a captura Arc 12"),
      // Pin de versão (S1 / D14). Vem do CONTEXTO, escrito pelo servidor no delegate —
      // nunca do payload de quem submete. Ausente ⇒ última publicada (comportamento
      // anterior ao pin), o que mantém os chamadores antigos funcionando.
      // Chega como STRING (o ContextStore guarda strings) ou número, se um chamador
      // programático o mandar. A conversão é EXPLÍCITA (`parsePin`), nunca `z.coerce`:
      // coerção transforma lixo em número plausível em silêncio, que é o modo de falha
      // que este arco inteiro existe para fechar.
      dialog_form_version: z.union([z.string(), z.number()]).optional()
        .describe("Versão PINADA do form (@ctx.core.workflow.dialog_form_version). Ausente ⇒ última publicada"),
      // Contrato antigo (4 campos nomeados) — mantido para os chamadores que ainda
      // mapeiam campo a campo. `answers` VENCE quando presente.
      classificacao:     z.string().optional().describe("Disposição crua: resolvido | pendente | escalado | cancelado"),
      resumo:            z.string().optional().describe("Resumo do atendimento (→ wrapup_summary, SEMPRE gravado)"),
      escalation_reason: z.string().optional().describe("Motivo da escalação (quando classificacao=escalado)"),
      proximos_passos:   z.string().optional().describe("Próximos passos (→ wrapup_next_steps, SEMPRE gravado)"),
      tenant_id:         z.string().optional(),
    } as any,
    // ⚠️ Este tipo é ESCRITO À MÃO, não inferido do schema acima — então campo novo
    // no schema que não entre aqui não chega ao handler, e o TypeScript avisa (foi o
    // que aconteceu ao acrescentar o pin). Mantê-los em par é obrigação de quem edita.
    async (args: {
      origin_session_id: string; segment_id: string; classificacao?: string
      answers?: Record<string, unknown>; dialog_form_id?: string
      dialog_form_version?: string | number
      resumo?: string; escalation_reason?: string; proximos_passos?: string; tenant_id?: string
    }) => {
      const { origin_session_id, segment_id } = args
      const tenant = args.tenant_id || defaultTenant

      // `answers` vence o argumento nomeado — é a fonte mais próxima do que o humano
      // digitou. Precedência declarada, e não "o que estiver preenchido": com dois
      // caminhos vivos, ordem implícita vira dado que muda conforme o chamador.
      const ans = args.answers ?? {}
      const pick = (key: string, legacy?: string): string => {
        const v = ans[key]
        if (v !== undefined && v !== null && v !== "") return String(v)
        return legacy ?? ""
      }
      const raw = pick("classificacao", args.classificacao).trim().toLowerCase()
      const outcome = WRAPUP_OUTCOME_MAP[raw]
      if (!outcome) {
        console.warn("[segment_outcome_record] unknown_classification: %j", args.classificacao)
        // Degradação nunca silenciosa: classificação desconhecida não vira placeholder.
        return mcpError("unknown_classification",
          `classificacao inválida: ${args.classificacao} (esperado: ${Object.keys(WRAPUP_OUTCOME_MAP).join("|")})`)
      }

      const key = segSignalKey(origin_session_id, segment_id)

      // ── 1. Acumula os campos DINÂMICOS no hash (mesma semântica de _apply_wrapup_to_segment) ──
      const dyn: Record<string, string> = { outcome, issue_status: raw }

      // Prosa do wrap-up — SEMPRE gravada (fix 2026-07-30). Antes, o texto que o
      // atendente digitou só era guardado quando `outcome !== "resolved"`; no caso
      // mais comum (resolvido) ele era descartado em silêncio, e a tela não dava
      // sinal nenhum disso. Colunas próprias, não `handoff_reason`: aquele campo
      // define `handoff_rate` (`countIf(handoff_reason != '') / count()`) e escrever
      // o resumo ali levaria a taxa de repasse a ~100% — trocaria uma perda muda por
      // uma métrica que muda de sentido sem avisar.
      const resumo          = pick("resumo", args.resumo)
      const proximosPassos  = pick("proximos_passos", args.proximos_passos)
      const escalationReason = pick("escalation_reason", args.escalation_reason)
      if (resumo)         dyn["wrapup_summary"]    = resumo
      if (proximosPassos) dyn["wrapup_next_steps"] = proximosPassos

      // `handoff_reason` segue EXATAMENTE como antes — mesma regra, mesmo formato.
      // Há sobreposição de texto com as colunas novas no caso não-resolvido, e é
      // deliberado: mudar este campo mudaria `handoff_rate` retroativamente.
      if (outcome !== "resolved") {
        const parts: string[] = []
        if (resumo) parts.push(resumo)
        if (proximosPassos) parts.push(`Próximos: ${proximosPassos}`)
        if (parts.length) dyn["handoff_reason"] = parts.join(" | ")
      }
      if (outcome === "escalated" && escalationReason) {
        dyn["escalation_reason"] = escalationReason
      }
      try {
        await redis.hset(key, dyn)
        await redis.expire(key, 604800)
        // last_outcome de sessão (último primary humano) — espelha o bridge.
        await redis.set(
          `session:${origin_session_id}:last_outcome`,
          JSON.stringify({ outcome, agent_kind: "human" }),
          "EX", 604800,
        )
      } catch (err) {
        return mcpError("redis_error", `hset seg_signal falhou: ${String(err)}`)
      }

      // ── 2. Lê o hash COMPLETO e re-publica participant_left (estáticos + dinâmicos) ──
      let h: Record<string, string> = {}
      try {
        h = await redis.hgetall(key)
      } catch (err) {
        return mcpError("redis_error", `hgetall seg_signal falhou: ${String(err)}`)
      }
      const segId = h["segment_id"]
      if (!segId) {
        console.warn(
          "[segment_outcome_record] seg_signal_not_seeded: origin=%s seg=%s (hash sem segment_id; outcome %s persistido, participant_left NÃO publicado)",
          origin_session_id, segment_id, outcome,
        )
        // Sem os campos ESTÁTICOS (o hook não semeou / segment_id errado): NÃO publica
        // (publicar linha parcial zeraria colunas no ReplacingMergeTree). Barulhento.
        return mcpOk({
          recorded: false, reason: "seg_signal_not_seeded", outcome,
          note: "hash sem segment_id (estáticos não semeados) — outcome persistido no hash, participant_left NÃO publicado para não corromper a linha do segmento",
        })
      }

      const participantId = h["instance_id"] || ""
      const event: Record<string, unknown> = {
        event_id:       randomUUID(),
        type:           "participant_left",
        session_id:     origin_session_id,
        tenant_id:      h["tenant_id"] || tenant,
        segment_id:     segId,
        participant_id: participantId,
        pool_id:        h["pool_id"] || "",
        agent_type_id:  h["agent_type_id"] || "",
        role:           "primary",
        agent_type:     "human",
        sequence_index: Number(h["sequence_index"] || 0),
        timestamp:      new Date().toISOString(),
      }
      // C1 — user_id derivado de human-{userId} (mesma regra do bridge).
      if (participantId.startsWith("human-")) {
        const uid = participantId.slice("human-".length)
        if (uid) event["user_id"] = uid
      }
      if (h["user_login"])   event["user_login"]   = h["user_login"]
      if (h["joined_at"])    event["joined_at"]    = h["joined_at"]
      if (h["duration_ms"] !== undefined && h["duration_ms"] !== "") {
        event["duration_ms"] = Number(h["duration_ms"])
      }
      event["outcome"]      = outcome
      event["issue_status"] = h["issue_status"] ?? raw
      if (h["handoff_reason"])    event["handoff_reason"]    = h["handoff_reason"]
      if (h["close_reason"])      event["close_reason"]      = h["close_reason"]
      if (h["escalation_reason"]) event["escalation_reason"] = h["escalation_reason"]
      if (h["wrapup_summary"])    event["wrapup_summary"]    = h["wrapup_summary"]
      if (h["wrapup_next_steps"]) event["wrapup_next_steps"] = h["wrapup_next_steps"]

      try {
        await kafka.publish(TOPIC_PARTICIPANTS, event)
      } catch (err) {
        return mcpError("publish_failed", `publish conversations.participants falhou: ${String(err)}`)
      }

      // ── 3. Fatia 3 — captura dirigida pelo form → agent.events (Arc 12) ────────
      //
      // DEPOIS do participant_left, de propósito: a disposição do segmento é o
      // contrato do wrap-up e não pode ser perdida por uma falha na captura. Falha
      // aqui é logada e devolvida em `agent_events_error`, nunca convertida em
      // `isError` — o wrap-up já foi gravado, e reportar erro faria o `on_failure`
      // do skill tratar como não-gravado.
      let agentEvents = 0
      let captureNote: string | undefined
      if (args.dialog_form_id && Object.keys(ans).length) {
        if (!dialogApiUrl) {
          captureNote = "dialog_api_url_ausente — captura Arc 12 desligada nesta instância"
          console.warn("[segment_outcome_record] %s", captureNote)
        } else {
          // Pin resolvido UMA vez: a mesma versão dirige a leitura do form e o
          // carimbo do evento. Resolver duas vezes abriria a porta para a leitura
          // e o rótulo discordarem — que é exatamente o defeito que o pin fecha.
          const pin = parsePin(args.dialog_form_version)
          if (args.dialog_form_version != null && pin == null) {
            console.warn(
              "[segment_outcome_record] pin de versão inválido (%j) para form=%s — " +
              "seguindo com a última publicada. O submit pode ler documento diferente " +
              "do que o atendente viu se houve publicação durante o ACW.",
              args.dialog_form_version, args.dialog_form_id,
            )
          }
          const form = await fetchPublishedForm(dialogApiUrl, tenant, args.dialog_form_id, pin)
          // `skill_id` da categoria = "wrapup", constante: o produtor É o wrap-up, e o
          // l2 existe para dizer QUEM emitiu. Usar o skill do workflow faria a série
          // quebrar a cada rename de skill, sem que a métrica tenha mudado.
          const derived = deriveAgentEvents(form, ans, {
            poolId:  h["pool_id"] || "",
            skillId: "wrapup",
          })
          if (form && !derived.length) {
            // Distinção que importa: form lido E sem captura declarada ≠ form não lido.
            captureNote = `form ${args.dialog_form_id} sem pergunta com capture.kind — nada a emitir`
            console.log("[segment_outcome_record] %s", captureNote)
          }
          for (const ev of derived) {
            try {
              await kafka.publish(TOPIC_AGENT_EVENTS, buildAgentBusinessEvent({
                tenant_id:     h["tenant_id"] || tenant,
                session_id:    origin_session_id,
                category:      ev.category,
                value:         ev.value,
                agent_type_id: h["agent_type_id"] || "human",
                skill_id:      "wrapup",
                pool_id:       h["pool_id"] || "",
                // Caminho A do Arc 12: o segmento é conhecido AQUI, por referência —
                // é a atribuição por participante que a fatia 2 preparou e que até
                // agora nenhum produtor exercitava.
                segment_id:    segId,
                instance_id:   participantId || null,
                // ── D14 — o VOCABULÁRIO que descreve esta categoria ──────────────
                // Sem isto a série é ilegível quando a forma do pool muda: medido
                // em 2026-09-05, `servico.troca_titularidade` (forma plana) e
                // `servico.cadastro.troca_titularidade` (forma em árvore) convivem
                // no MESMO pool e no MESMO dia, e nada na linha diz qual é qual.
                //
                // Vai em `tags` porque a coluna já existe (`Map(String,String)` em
                // `agent_business_events`) — nenhum DDL, nenhum consumer novo. A
                // versão só entra quando há PIN: sem ele, carimbar "a última
                // publicada" seria afirmar uma precisão que a leitura não teve.
                tags: {
                  dialog_form_id: args.dialog_form_id,
                  ...(pin != null ? { dialog_form_version: String(pin) } : {}),
                },
              }))
              agentEvents++
            } catch (err) {
              captureNote = `publish agent.events falhou: ${String(err)}`
              console.warn("[segment_outcome_record] %s", captureNote)
            }
          }
        }
      }

      console.log(
        "[segment_outcome_record] recorded origin=%s seg=%s outcome=%s (participant_left published, agent_events=%d)",
        origin_session_id, segId, outcome, agentEvents,
      )
      return mcpOk({
        recorded: true, segment_id: segId, outcome, session_id: origin_session_id,
        agent_events: agentEvents,
        ...(captureNote ? { capture_note: captureNote } : {}),
      })
    },
  )
}
