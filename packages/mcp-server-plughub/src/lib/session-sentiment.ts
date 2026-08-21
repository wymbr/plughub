/**
 * lib/session-sentiment.ts
 * Fonte única do sentimento exibido ao operador.
 *
 * ── Por que este módulo existe ───────────────────────────────────────────────
 * O sentimento tinha DUAS implementações independentes e idênticas — a tool MCP
 * `supervisor_state` (`tools/supervisor.ts`) e o endpoint HTTP
 * `GET /api/supervisor_state/:sessionId` (`server.ts`), que é o que a Console
 * realmente consome. Ambas liam
 *
 *     session:{id}:ai → current_turn.partial_params.sentiment_score
 *
 * que é o auto-reporte do `output_schema` do skill, **aposentado em 2026-08-23**
 * porque nenhum skill declara o campo. E ambas faziam `Number(… ?? 0)`.
 *
 * Os dois defeitos se compõem: a fonte não tem valor, e o `?? 0` converte
 * NÃO-MEDIDO em `0.0`, que é um ponto LEGÍTIMO da escala e classifica como
 * `neutral`. Resultado medido em 2026-08-24: cliente com score real **-0.50** no
 * ContextStore, e a barra da Console anunciando **"Neutral"** com toda a cara de
 * normalidade. Pior que um erro visível — é a plataforma exibindo confiança sobre
 * um número que ela não tem.
 *
 * O agravante que dá o nome à regra: a UI **se protegia**. O chip da `ActionBar`
 * só renderiza com `sentimentScore !== null` (e a faixa do `ChatArea` inventara
 * `!== 0` pelo mesmo instinto). O `?? 0` do backend desarmou a proteção antes que
 * ela pudesse agir. *Um default no produtor derruba a guarda do consumidor sem
 * deixar rastro nenhum.*
 *
 * ── Fonte canônica ───────────────────────────────────────────────────────────
 * ContextStore da sessão: `{tenant_id}:ctx:{session_id}`, tag
 * `session.sentimento.current`, escrita por
 * `ai-gateway/sentiment_emitter.write_context_store_sentiment`.
 *
 * É superconjunto ESTRITO da fonte antiga, e isso é o que decide a questão: TODO
 * caminho que produz um score passa por `session.update_partial_params`, que
 * chama `write_context_store_sentiment` — inclusive o auto-reporte do
 * `output_schema`, quando algum skill voltar a declará-lo. Ler as duas fontes
 * seria redundante *e* perderia dado; ler só o ctx não perde nada.
 *
 * ⚠️ Ler sempre do hash **CRU**, nunca do snapshot já filtrado por
 * `applyContextMaskingDynamic`. O filtro do snapshot é por NAMESPACE do operador
 * (`context_visibility.operator_namespaces`, configurável POR POOL): o default
 * inclui `session`, mas um pool que estreite a lista apagaria o sentimento em
 * silêncio — e "chip sumiu" é indistinguível de "não houve medição". Sentimento
 * não é PII; sua `visibility` é `agents_only`, e o operador É um agente.
 */

/** Tag do ContextStore que guarda o score medido. Contrato com o ai-gateway. */
export const SENTIMENT_CTX_TAG = "session.sentimento.current"

/** Limiar de alerta. Espelha `sentiment.alert` da spec 3.2a. */
export const SENTIMENT_ALERT_THRESHOLD = -0.5

export type SentimentTrend = "improving" | "stable" | "declining"

export interface SentimentView {
  /** Score medido, -1..+1. **`null` = NÃO MEDIDO** — nunca 0, que é "neutro". */
  current:    number | null
  /** Histórico de pontos MEDIDOS, do mais antigo ao mais recente. */
  trajectory: number[]
  /** `null` quando não há histórico suficiente para uma tendência honesta. */
  trend:      SentimentTrend | null
  alert:      boolean
}

/**
 * Trajetória vazia — **não há produtor de histórico de sentimento**.
 *
 * O ContextStore guarda só o valor CORRENTE (sobrescrito a cada turno), e
 * `consolidated_turns` não serve de substituto: o `float(… or 0.0)` de
 * `session.update_partial_params` já achatou lá dentro, tornando um `0.0` medido
 * indistinguível de um turno sem medição. Filtrá-lo por `!== 0` seria repetir a
 * mesma mentira um nível abaixo.
 *
 * `CLAUDE.md` § Sentiment Tracking documenta um array `session:{id}:sentiment`
 * para isto — e **nenhum componente o escreve**. Enquanto for assim, gráfico e
 * seta de tendência ficam ausentes em vez de fabricados. Ver `TODO.md`.
 */
export const NO_MEASURED_TRAJECTORY: readonly number[] = []

/**
 * Extrai o score do hash CRU do ContextStore.
 *
 * Devolve `null` em qualquer caso de ausência ou ilegibilidade — e os motivos
 * são LOGADOS separadamente, porque são fatos diferentes: tag ausente é o estado
 * normal de uma sessão sem medição; tag presente e ilegível é defeito de
 * produtor. Colapsá-los num `null` mudo recriaria a cegueira que este módulo
 * existe para desfazer.
 *
 * O valor guardado é um `ContextEntry` (`{value, confidence, source, …}`), NÃO um
 * escalar — comparar o registro inteiro como número é o erro que já custou um
 * gate falso-negativo em 2026-08-24.
 */
export function parseCtxSentiment(
  hash: Record<string, string> | null | undefined,
  logPrefix = "[sentiment]",
): number | null {
  const raw = hash?.[SENTIMENT_CTX_TAG]
  if (raw === undefined || raw === null || raw === "") return null

  let parsed: unknown
  try {
    parsed = JSON.parse(raw)
  } catch {
    console.warn(
      `${logPrefix} ${SENTIMENT_CTX_TAG} presente mas não é JSON — tratando como ` +
      `NÃO MEDIDO. Isto é defeito de produtor, não sessão sem medição.`
    )
    return null
  }

  // Formato canônico: ContextEntry { value, confidence, source, visibility, updated_at }
  if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
    const value = (parsed as Record<string, unknown>)["value"]
    if (typeof value === "number" && Number.isFinite(value)) return value
    console.warn(
      `${logPrefix} ${SENTIMENT_CTX_TAG}.value não é número finito ` +
      `(${JSON.stringify(value)}) — tratando como NÃO MEDIDO.`
    )
    return null
  }

  // Tolerância a escalar cru: nenhum produtor escreve assim hoje, mas se algum
  // passar a escrever, o valor é aproveitado E denunciado — em vez de sumir.
  if (typeof parsed === "number" && Number.isFinite(parsed)) {
    console.warn(
      `${logPrefix} ${SENTIMENT_CTX_TAG} veio como escalar cru, fora do contrato ` +
      `ContextEntry. Valor aproveitado; corrigir o produtor.`
    )
    return parsed
  }

  console.warn(
    `${logPrefix} ${SENTIMENT_CTX_TAG} em formato desconhecido — NÃO MEDIDO.`
  )
  return null
}

/**
 * Tendência sobre pontos MEDIDOS. `null` com menos de 3 pontos.
 *
 * O default anterior era `"stable"`, que soa como leitura e não é: sem histórico,
 * "estável" é tão inventado quanto o `0.0` era.
 */
export function computeTrend(trajectory: readonly number[]): SentimentTrend | null {
  if (trajectory.length < 3) return null
  const window    = Math.min(3, Math.floor(trajectory.length / 2))
  const firstAvg  = trajectory.slice(0, window).reduce((a, b) => a + b, 0) / window
  const recentAvg = trajectory.slice(-window).reduce((a, b) => a + b, 0) / window
  const delta     = recentAvg - firstAvg
  if (delta >  0.1) return "improving"
  if (delta < -0.1) return "declining"
  return "stable"
}

/**
 * Monta a view a partir do hash CRU do ContextStore.
 *
 * `trajectory` é parâmetro — e não leitura interna — para que o dia em que
 * houver produtor de histórico mude UM lugar. Hoje todos os chamadores passam
 * `NO_MEASURED_TRAJECTORY`.
 */
export function sentimentFromCtxHash(
  hash:       Record<string, string> | null | undefined,
  trajectory: readonly number[] = NO_MEASURED_TRAJECTORY,
  logPrefix = "[sentiment]",
): SentimentView {
  const current = parseCtxSentiment(hash, logPrefix)
  return {
    current,
    trajectory: [...trajectory],
    trend:      computeTrend(trajectory),
    alert:      current !== null && current < SENTIMENT_ALERT_THRESHOLD,
  }
}
