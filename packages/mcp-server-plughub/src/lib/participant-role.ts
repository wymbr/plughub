/**
 * participant-role.ts
 * Papel de PARTICIPAÇÃO do participante NESTA sessão, lido do roster.
 *
 * ── Por que este arquivo existe (MEN-01/MEN-02, 2026-09-12) ──────────────────
 * A função morava privada em `tools/session.ts` e servia só ao caminho MCP. Com a
 * decisão de que *quem conduz menciona* passa a valer também no WebSocket do
 * Console, o caminho do `server.ts` precisa da MESMA resposta — e duas cópias da
 * mesma pergunta é como a regra de @mention virou "garantia de uma porta só".
 * Precedente direto: `routeMentions` já foi extraída para `lib/mention-routing.ts`
 * pelo mesmo motivo (F5 do ADR de identidade por-pool), e `lib/session-sentiment.ts`
 * por outro — *um cálculo, uma casa*.
 *
 * ── Por que o roster e não o hash da instância (§1055, Fatia B — 2026-08-05) ──
 * Papel de participação é fato de **(participante, sessão)**. A mesma instância
 * atende `max_concurrent_sessions` sessões e pode ser `primary` numa e `specialist`
 * noutra ao mesmo tempo — guardá-lo em `{t}:agent:instance:{id}` colapsaria
 * multi-sessão, e foi por isso que NENHUM produtor jamais escreveu lá. Os dois
 * leitores liam um campo sem escritor e caíam no default desde sempre. Ver
 * CLAUDE.md § "Never store a narrower-scope fact in a wider-scope field".
 *
 * ── `resolved` é o que torna isto usável como gate ───────────────────────────
 * Ele distingue "li e vale primary" de "não consegui ler". O default `primary`
 * existe para os consumidores históricos (author do stream, decisão de
 * mascaramento); um gate de autorização NUNCA pode usá-lo sem olhar `resolved`,
 * porque um gate que falha aberto não é gate.
 *
 * ⚠️ **A chave de busca é `participant_id`.** O roster (escrito por
 * `orchestrator-bridge/_upsert_participant_roster`) carrega também `instance_id`, e
 * medido em 2026-09-12 os dois são IGUAIS para `human` e para `native`. Casar pelos
 * dois faria o gate autorizar onde hoje ele não resolve — direção errada para uma
 * decisão de autorização. Se algum dia divergirem, o sintoma é o gate deixando de
 * rotear (falha FECHADA, com log), nunca o contrário.
 */
import type { RedisClient } from "../infra/redis"

export type ParticipantRole = { role: string; resolved: boolean }

export async function resolveParticipantRole(
  redis:         RedisClient,
  sessionId:     string,
  participantId: string,
): Promise<ParticipantRole> {
  try {
    const raw = await redis.get(`session:${sessionId}:participants`)
    if (!raw) {
      // Sem roster: sessão anterior ao produtor (§1055 Fatia B), ou TTL vencido.
      // Degradação COM log — muda aqui reproduz exatamente o defeito que a fatia
      // fechou, e o sintoma (tudo `primary`) é plausível demais para ser notado.
      console.warn(
        `[role] roster ausente: session=${sessionId} participant=${participantId} ` +
        `— caindo em 'primary' (não-resolvido)`
      )
      return { role: "primary", resolved: false }
    }
    const list = JSON.parse(raw) as Array<Record<string, unknown>>
    const hit  = Array.isArray(list)
      ? list.find(p => p && p["participant_id"] === participantId)
      : undefined
    if (hit && typeof hit["role"] === "string" && hit["role"]) {
      return { role: hit["role"] as string, resolved: true }
    }
    // Roster existe e o participante NÃO está nele. O caso conhecido é o
    // especialista de conferência via SDK externo, que recebe um `uuid4()` efêmero
    // nunca persistido (orchestrator-bridge §3338) — o pré-requisito 1 do §1055,
    // ainda aberto. Nomear no log evita que vire "o roster não funciona".
    console.warn(
      `[role] participante fora do roster: session=${sessionId} ` +
      `participant=${participantId} roster=${list.length} entradas ` +
      `— caindo em 'primary' (não-resolvido)`
    )
    return { role: "primary", resolved: false }
  } catch (err) {
    console.warn(
      `[role] leitura do roster falhou: session=${sessionId} ` +
      `participant=${participantId} — ${String(err)}`
    )
    return { role: "primary", resolved: false }
  }
}

/**
 * Igual à de cima, mas casando pela **instância** — e a diferença não é de gosto, é de
 * quem escolhe a identidade.
 *
 * ⚠️ **Um gate não pode decidir sobre identidade DECLARADA pelo chamador.** O
 * `message_send` recebe `participant_id` no INPUT e verifica o `session_token` ao lado;
 * resolver o papel pelo primeiro deixa qualquer portador de token nomear um participante
 * que seja `primary` no roster e mencionar como ele. É o mesmo defeito estrutural que
 * derrubou o gate do avaliador em 2026-09-01 (CAP-01), onde a recusa dependia de um
 * `participant_id` vindo do input com o `instance_id` assinado em mãos — e a lição de lá
 * é que o gate inteiro SAIU, porque consertar exigia um cenário que o justificasse.
 * Aqui o cenário existe (a regra foi decidida em 2026-09-12), então o conserto é usar a
 * identidade que viaja ASSINADA.
 *
 * O roster carrega `instance_id` em toda entrada (medido em 2026-09-12: `human` e
 * `native`), e é por ele que se casa. **Sem fallback para `participant_id`**: um fallback
 * aqui devolveria ao chamador a escolha da identidade, que é exatamente o que esta função
 * existe para tirar dele. Não encontrou ⇒ `resolved: false` ⇒ o gate nega, com log.
 */
export async function resolveRoleByInstance(
  redis:      RedisClient,
  sessionId:  string,
  instanceId: string,
): Promise<ParticipantRole> {
  if (!instanceId) {
    console.warn(
      `[role] sem instance_id assinado: session=${sessionId} — não há identidade ` +
      `que o chamador não possa escolher; tratando como não-resolvido`
    )
    return { role: "primary", resolved: false }
  }
  try {
    const raw = await redis.get(`session:${sessionId}:participants`)
    if (!raw) {
      console.warn(
        `[role] roster ausente: session=${sessionId} instance=${instanceId} ` +
        `— caindo em 'primary' (não-resolvido)`
      )
      return { role: "primary", resolved: false }
    }
    const list = JSON.parse(raw) as Array<Record<string, unknown>>
    const hit  = Array.isArray(list)
      ? list.find(p => p && p["instance_id"] === instanceId)
      : undefined
    if (hit && typeof hit["role"] === "string" && hit["role"]) {
      return { role: hit["role"] as string, resolved: true }
    }
    console.warn(
      `[role] instância fora do roster: session=${sessionId} instance=${instanceId} ` +
      `roster=${Array.isArray(list) ? list.length : 0} entradas — não-resolvido`
    )
    return { role: "primary", resolved: false }
  } catch (err) {
    console.warn(
      `[role] leitura do roster falhou: session=${sessionId} instance=${instanceId} ` +
      `— ${String(err)}`
    )
    return { role: "primary", resolved: false }
  }
}

/**
 * O gate de @mention, em UMA casa: *quem CONDUZ menciona; quem foi CONVIDADO não
 * convida.* Decidido pelo dono em 2026-09-12 (MEN-01), depois da análise de cenário
 * que a ficha exigia.
 *
 * **O eixo é POSIÇÃO, nunca espécie.** O repositório afirmou por meses *"IA nunca
 * emite @mention"* em quatro lugares, e o código nunca impôs isso: `primary` é
 * posição na sessão, e a IA que conduz a conversa É a `primary`. O modelo foi
 * medido em 2026-09-12 e confere — 597 sessões com 1 primary, 456 com 2, 229 com 3;
 * dos 222 pares sobrepostos, 220 duram ≤ 100 ms (a costura da passagem de bastão).
 * Humano e IA são simétricos no modelo de sessão, e esta função os trata igual.
 *
 * **O que ela contém**, e é a única coisa que se defende: `specialist`,
 * `supervisor` e `evaluator` não convidam. Sem isso, um convidado convida outro em
 * cadeia, e o único limite vira o `mentionable_pools` do pool de quem emite.
 *
 * Falha FECHADA: sem leitura positiva do roster, não roteia.
 */
export function mayRouteMentions(r: ParticipantRole): boolean {
  return r.resolved && r.role === "primary"
}
