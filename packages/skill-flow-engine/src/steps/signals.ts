/**
 * steps/signals.ts — sinais da PLATAFORMA para um step bloqueado (menu, resolve).
 *
 * Chegam na fila `menu:signal:{sid}[:{iid}]` (`redisKeys.menuSignal`), que só o
 * orchestrator-bridge escreve. MEN-07 (2026-09-16): viajavam em `menu:result`, junto da resposta
 * do cliente, e eram reconhecidos por `JSON.parse` do texto — o cliente forjava um salto de passo
 * digitando o JSON. Agora a FILA diz de quem é a mensagem; nada em `menu:result` é interpretado.
 */

export type PlatformSignal =
  | { kind: "trigger_step"; step: string }   // @mention trigger_step
  | { kind: "terminate" }                     // @mention terminate_self
  // desfecho da coleta por voz/teclado. `aborted` (NIV-07): o CANAL não conseguiu garantir a
  // coleta protegida — ex.: não tirou da sala quem ouviria a tecla mascarada — e a desfez
  | { kind: "collect"; outcome: "timeout" | "invalid" | "aborted" }
  | { kind: "unknown"; raw: string }          // sinal ilegível: quem o recebe decide, DITO

export function parseSignal(raw: string): PlatformSignal {
  let parsed: unknown
  try {
    parsed = JSON.parse(raw)
  } catch {
    return { kind: "unknown", raw }
  }
  if (!parsed || typeof parsed !== "object") return { kind: "unknown", raw }
  const p = parsed as Record<string, unknown>
  if (typeof p["_mention_trigger_step"] === "string" && p["_mention_trigger_step"]) {
    return { kind: "trigger_step", step: p["_mention_trigger_step"] as string }
  }
  if (p["_mention_terminate"] === true) return { kind: "terminate" }
  if (p["_collect_outcome"] === "timeout" || p["_collect_outcome"] === "invalid"
      || p["_collect_outcome"] === "aborted") {
    return { kind: "collect", outcome: p["_collect_outcome"] }
  }
  return { kind: "unknown", raw }
}
