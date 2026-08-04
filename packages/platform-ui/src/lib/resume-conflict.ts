/**
 * lib/resume-conflict.ts — leitura do 409 do resume (Fase F / ADR § D7, F2).
 *
 * A Fase F fez o resume ser TERMINAL-UMA-VEZ: o segundo a chegar (submit do
 * agente, expire do supervisor ou varredura de prazo) recebe **409 com a causa
 * nomeada**, em vez do 404 que afirmava "seu token não existe" — a frase errada,
 * dita justamente a quem tinha o trabalho na mão. Este módulo é o lado da tela
 * dessa promessa: sem ele o 409 chega e ninguém o lê.
 *
 * DOIS FORMATOS, UM PARSER. O corpo nasce igual nos dois caminhos
 * (`ResumeAlreadyTerminalError.as_detail`), mas chega com profundidades
 * diferentes:
 *
 *   · agente   → posta direto no gateway; FastAPI embrulha uma vez:
 *                {detail: {error:"resume_already_terminal", …}}
 *   · supervisor → passa pelo mcp-server, que embrulha DE NOVO:
 *                {error:"expire_failed", detail:{detail:{…}}}
 *
 * Por isso o parser DESCE por `detail` procurando o discriminador, em vez de ler
 * uma profundidade fixa: fixar o nível faria um salto novo no caminho (um proxy,
 * um BFF) devolver a tela ao estado de hoje, e sem nada ficar vermelho.
 *
 * CAMPO AUSENTE NÃO VIRA FRASE. No ramo `in_flight` o backend só conhece o
 * detentor do lock — `session_id`, `cause` e `closed_at` chegam VAZIOS (medido
 * em 2026-08-04). Uma frase única com buracos sairia como "encerrado por agent
 * () em ", que é pior que não dizer. Daí a separação: a SENTENÇA depende só do
 * `state` (e é do consumidor, porque o que se perde difere — o agente perde
 * respostas, o supervisor não perde nada), e os FATOS vão numa linha à parte,
 * montada só com o que existe.
 */

/** Como o outro encerramento estava quando este foi recusado. */
export type ResumeConflictState = 'in_flight' | 'terminal'

export interface ResumeConflict {
  /** `in_flight` = corrida real (outro está no meio); `terminal` = já acabou. */
  state:     ResumeConflictState
  /** Só no ramo `terminal` — vazio em `in_flight`. */
  sessionId: string
  /** Quem encerrou (ou está encerrando). Nunca vazio no fio, mas pode vir "?". */
  closedBy:  string
  /** `task_done` | `acw_expired` | `acw_supervisor_closed`. Vazio em `in_flight`. */
  cause:     string
  /** ISO do encerramento. Vazio em `in_flight`. */
  closedAt:  string
}

/** Assinatura mínima do `t`. Helper fora de componente NUNCA chama useTranslation. */
export type TFunc = (key: string, opts?: Record<string, unknown>) => string

/** Discriminador gravado por `ResumeAlreadyTerminalError.as_detail()`. */
const MARKER = 'resume_already_terminal'

/** Teto da descida — protege contra corpo cíclico/absurdo sem esconder nada. */
const MAX_DEPTH = 6

function str(x: unknown): string {
  return typeof x === 'string' ? x.trim() : ''
}

/**
 * Extrai o conflito de um corpo de erro, seja qual for o número de saltos.
 * Devolve `null` quando o corpo NÃO é um conflito de resume — e nesse caso o
 * chamador deve cair na mensagem genérica, nunca inventar um estado.
 */
export function parseResumeConflict(body: unknown): ResumeConflict | null {
  let node: unknown = body
  for (let depth = 0; depth < MAX_DEPTH; depth++) {
    if (!node || typeof node !== 'object') return null
    const o = node as Record<string, unknown>
    if (o['error'] === MARKER) {
      const raw = str(o['state'])
      if (raw !== 'in_flight' && raw !== 'terminal') {
        // Estado que esta versão da tela não conhece. Degrada para a mensagem
        // genérica — mas BARULHENTO: um `state` novo no backend tem de aparecer
        // para quem for depurar, e não sumir dentro de um "HTTP 409".
        console.warn(`[resume-conflict] estado desconhecido no 409: ${raw || '(vazio)'}`)
        return null
      }
      return {
        state:     raw,
        sessionId: str(o['session_id']),
        closedBy:  str(o['closed_by']),
        cause:     str(o['cause']),
        closedAt:  str(o['closed_at']),
      }
    }
    node = o['detail']
  }
  return null
}

/**
 * Rótulo de QUEM encerrou. O valor do fio é um ator, não um nome de pessoa:
 * `human:{user_id}` (principal verificado), ou o `source` do payload
 * (`agent`, `operator`, `timeout_scanner`, `supervisor:{id}`, `external`).
 *
 * Traduz o que é vocabulário fechado da plataforma e deixa passar o resto CRU —
 * um id opaco é feio, mas é acionável; trocá-lo por "alguém" apagaria a única
 * informação que permite ir atrás.
 */
export function resumeActorLabel(raw: string, t: TFunc): string {
  const v = raw.trim()
  if (!v || v === '?') return t('common:resumeConflict.actor.unknown')
  if (v.startsWith('human:'))      return v.slice('human:'.length) || t('common:resumeConflict.actor.unknown')
  if (v.startsWith('supervisor:')) return v.slice('supervisor:'.length) || t('common:resumeConflict.actor.unknown')
  if (v === 'agent' || v === 'operator' || v === 'timeout_scanner' || v === 'external') {
    return t(`common:resumeConflict.actor.${v}`)
  }
  return v
}

/** Rótulo da causa. Vazio quando não há causa registrada (o ramo `in_flight`). */
export function resumeCauseLabel(raw: string, t: TFunc): string {
  const v = raw.trim()
  if (!v || v === '?') return ''
  if (v === 'task_done' || v === 'acw_expired' || v === 'acw_supervisor_closed') {
    return t(`common:resumeConflict.cause.${v}`)
  }
  return v
}

/**
 * Linha de FATOS — "Encerrado por: X · Motivo: Y · Em: Z", com cada parte
 * omitida quando o campo não veio. Devolve string vazia quando nada veio, e aí
 * o consumidor mostra só a sentença.
 */
export function resumeConflictDetails(c: ResumeConflict, t: TFunc): string {
  const parts: string[] = []

  const who = c.closedBy.trim()
  if (who && who !== '?') {
    const label = c.state === 'terminal'
      ? t('common:resumeConflict.by')
      : t('common:resumeConflict.byInFlight')
    parts.push(`${label}: ${resumeActorLabel(who, t)}`)
  }

  const cause = resumeCauseLabel(c.cause, t)
  if (cause) parts.push(`${t('common:resumeConflict.reason')}: ${cause}`)

  if (c.closedAt) {
    const d = new Date(c.closedAt)
    if (!Number.isNaN(d.getTime())) {
      parts.push(`${t('common:resumeConflict.at')}: ${d.toLocaleString()}`)
    }
  }

  return parts.join(' · ')
}
