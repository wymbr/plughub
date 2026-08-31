/**
 * PullInboxPanel — Frente 1 F2b-2a/2b: inbox do dispatch pull.
 *
 * Lista os contatos CLAIMÁVEIS das filas pull em que o agente está logado
 * (poll de GET /api/work_queue/list). Clicar na LINHA abre o preview read-only
 * (onPreview); o botão "Pull" faz o claim direto (POST /api/work_queue/claim).
 * No sucesso, o contato anexa pelo fluxo existente (conversation.assigned via WS)
 * → vira atendimento normal.
 *
 * F2b-2b-2 (polish): idade ao vivo (tick 1s), cor por SLA (idade×sla_target),
 * gating de capacidade (claimDisabled), e auto-clear do preview quando o contato
 * previewado sai da fila (reivindicado por outro / timeout).
 */
import React, { useCallback, useEffect, useRef, useState } from "react"
import { useTranslation } from "react-i18next"
import { ChevronRight } from "lucide-react"
import { poolDisplayLabel } from "../poolLabel"
import { apiFetch } from '@/api/apiFetch'

interface QueueContact {
  session_id:   string
  pool_id:      string
  state:        string
  channel:      string | null
  summary:      string | null
  queued_at_ms: number | null
  age_ms:       number | null
  // P3 — primeiro enqueue (preservado no re-enfileiramento). A idade exibida
  // deriva dele; queued_at_ms reseta na devolução à fila e mentiria a espera.
  first_queued_ms: number | null
  // Bug B fix: carried back from the queued contact so the claim attaches the
  // human to the caller's conference (not as a bare primary). Null/"" = none.
  conference_id: string | null
  // Camada B (pull direcionado / "ramal"): reserva a um recurso preferido.
  // assigned_to = user_id (null = fila compartilhada); fallback_to_pool_after_s =
  // janela da reserva em s (null = permanente); assigned_at_ms = âncora da janela.
  assigned_to:              string | null
  fallback_to_pool_after_s: number | null
  assigned_at_ms:           number | null
  // Wrap-up unificado (Camada E2): true = auto-atendimento (o Console reivindica
  // sozinho o item reservado a mim, entrega inline). null/false = pull manual.
  auto_attend:              boolean | null
}

interface PullInboxPanelProps {
  /** Pools pull em que o agente está logado (accessible ∩ dispatch_mode=pull). */
  pullPools:    string[]
  /** Instância do agente para o claim (ex.: human-{userId}). */
  instanceId:   string
  /** SLA target (ms) por pool — para a cor de urgência das linhas. */
  poolSla?:     Record<string, number | null>
  // `claimDisabled`/`claimDisabledReason` REMOVIDAS em 2026-08-04: eram props mortas
  // (declaradas, desestruturadas, nunca usadas no render) — sobra do tempo em que a
  // LINHA tinha botão de Pull. Hoje a linha só abre o preview, e o gate de capacidade
  // vive no botão da barra do preview (`AgentAssistPage`, `disabled={atCapacity}`).
  // Prop aceita e ignorada é pior que prop ausente: promete um gate que não existe.
  /** Chamado após um claim bem-sucedido (sessionId) — ex.: selecionar na lista. */
  onClaimed?:   (sessionId: string) => void
  /** F2b-2b — clicar na linha abre o preview read-only (sem claim). */
  onPreview?:   (sessionId: string, poolId: string, conferenceId: string) => void
  /** Sessão em preview no momento (highlight da linha). */
  previewSessionId?: string | null
  /** F2b-2b-2 — a sessão em preview saiu da fila (claim de outro / timeout). */
  onPreviewInvalid?: () => void
  /** Intervalo de polling em ms (default 4000). */
  pollMs?:      number
  /** P4 — bump deste número força um refresh imediato (ex.: logo após um
   * release/"Return to queue", sem esperar o próximo poll). */
  refreshSignal?: number
}

function fmtAge(ms: number | null): string {
  if (ms == null) return ""
  const s = Math.max(0, Math.floor(ms / 1000))
  if (s < 60) return `${s}s`
  const m = Math.floor(s / 60)
  return `${m}m ${s % 60}s`
}

/**
 * Rótulo da fila (D3). A regra vive em `poolLabel.ts` — esta cópia local do sufixo foi o
 * que deixou a barra do contato divergir da fila: mesmo item, dois nomes na mesma tela.
 */
const queueLabel = poolDisplayLabel

// Cor por urgência: idade relativa ao SLA do pool. Sem SLA → neutro.
function slaColor(ageMs: number | null, slaMs: number | null | undefined): string {
  if (ageMs == null || !slaMs || slaMs <= 0) return "text-muted-light"
  const r = ageMs / slaMs
  if (r >= 1)   return "text-red-text"
  if (r >= 0.6) return "text-warning-text"
  return "text-green-text"
}

export const PullInboxPanel: React.FC<PullInboxPanelProps> = ({
  pullPools, instanceId, poolSla,
  onClaimed, onPreview, previewSessionId, onPreviewInvalid, pollMs = 4000,
  refreshSignal,
}) => {
  const { t } = useTranslation("agentAssist")
  const [contacts, setContacts] = useState<QueueContact[]>([])
  const [claiming, setClaiming] = useState<string | null>(null)
  const [error, setError]       = useState<string | null>(null)
  // RECUSA DE CLAIM em estado PRÓPRIO, separado do erro de listagem.
  //
  // Antes os dois dividiam `error`, e o `refresh()` que segue a recusa (§ handlePull)
  // faz `setError(null)` no caminho feliz — ou seja, o motivo da recusa era apagado
  // pela linha seguinte, vivendo o tempo de um fetch. Contra a invariante do
  // CLAUDE.md: "degradação NUNCA é silenciosa". Hoje só o auto-atendimento de
  // wrap-up passa por ali, e é justamente onde o silêncio custa caro — o agente não
  // clicou em nada, então não tem por que suspeitar que algo falhou.
  //
  // A recusa persiste até o próprio item sair da fila (foi resolvido de algum jeito)
  // ou até o agente dispensá-la. Um refresh de lista NÃO a apaga: o que a torna
  // obsoleta é o item sumir, não a lista recarregar.
  const [claimError, setClaimError] = useState<{ sessionId: string; text: string } | null>(null)
  const [nowMs, setNowMs]       = useState<number>(Date.now())
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set())

  const toggleGroup = useCallback((poolId: string) => {
    setCollapsed(prev => {
      const next = new Set(prev)
      if (next.has(poolId)) next.delete(poolId); else next.add(poolId)
      return next
    })
  }, [])

  // Tick de 1s — idade na fila avança ao vivo entre os polls.
  useEffect(() => {
    const id = setInterval(() => setNowMs(Date.now()), 1000)
    return () => clearInterval(id)
  }, [])

  const refresh = useCallback(async () => {
    if (pullPools.length === 0) { setContacts([]); return }
    try {
      const url = `/api/work_queue/list?pools=${encodeURIComponent(pullPools.join(","))}`
      // Fila é estado tempo-real; o Express gera ETag no res.json() e o browser
      // devolve 304 (corpo em cache) — o que faz o refresh imediato pós-release
      // (P4) ler a lista velha. no-store força 200 com dados frescos sempre.
      const res = await apiFetch(url, { cache: "no-store" })
      if (!res.ok) { setError(`HTTP ${res.status}`); return }
      const data = await res.json() as { contacts?: QueueContact[] }
      setContacts(data.contacts ?? [])
      setError(null)
    } catch (e) {
      setError(String(e))
    }
  }, [pullPools])

  useEffect(() => {
    refresh()
    const id = setInterval(refresh, pollMs)
    return () => clearInterval(id)
  }, [refresh, pollMs])

  // P4 — refresh imediato quando o pai sinaliza (ex.: pós-release), sem esperar
  // o poll. Ignora o mount inicial (o efeito de poll acima já faz o 1º fetch).
  const didMountRef = useRef(false)
  useEffect(() => {
    if (!didMountRef.current) { didMountRef.current = true; return }
    refresh()
  }, [refreshSignal, refresh])

  // Auto-clear do preview: se o contato previewado SAIU da fila (estava e não
  // está mais — claim de outro agente ou timeout), avisa o pai para limpar.
  const prevIdsRef = useRef<Set<string>>(new Set())
  useEffect(() => {
    const ids = new Set(contacts.map(c => c.session_id))
    if (previewSessionId && prevIdsRef.current.has(previewSessionId) && !ids.has(previewSessionId)) {
      onPreviewInvalid?.()
    }
    prevIdsRef.current = ids
  }, [contacts, previewSessionId, onPreviewInvalid])

  const ageOf = useCallback((c: QueueContact): number | null => {
    // P3 — espera REAL = desde o primeiro enqueue (preservado no re-enfileiramento);
    // queued_at_ms reseta na devolução à fila, então só serve de fallback.
    const base = c.first_queued_ms ?? c.queued_at_ms
    if (base != null) return Math.max(0, nowMs - base)
    return c.age_ms
  }, [nowMs])

  // Camada B (ramal): user_id deste agente, derivado do instanceId (`human-{userId}`).
  const myUserId = instanceId.startsWith("human-") ? instanceId.slice("human-".length) : instanceId

  // Estado de reserva de um item. O árbitro (routing-engine) é a autoridade no
  // claim; aqui só filtramos/rotulamos o inbox:
  //   shared        — sem assigned_to (fila compartilhada).
  //   reservedToMe  — assigned_to == este agente.
  //   overflowed    — reservado a outro, mas a janela (fallback) já expirou → claimable.
  //   reservedOther — reservado a outro e ainda na janela → NÃO exibir (não é meu).
  const reservationOf = useCallback((c: QueueContact): "shared" | "reservedToMe" | "reservedToMeExpired" | "overflowed" | "reservedOther" => {
    if (!c.assigned_to) return "shared"
    if (c.assigned_to === myUserId) {
      // A janela vale para a MINHA reserva também. Passado
      // `fallback_to_pool_after_s`, o árbitro libera o item a qualquer um do pool
      // (o gate vive dentro do `work_task_claim`), e continuar exibindo "Reservado
      // a você" afirmaria uma exclusividade que acabou.
      //
      // Assimetria encontrada em 2026-08-04, ao validar a Fase C na tela: o
      // transbordo era calculado só para a reserva ALHEIA (logo abaixo), e o
      // próprio dono via o crachá para sempre. Valor plausível — ninguém olha um
      // rótulo que diz o que se espera dele.
      const anchorMine = c.assigned_at_ms ?? c.first_queued_ms ?? c.queued_at_ms
      const expiredMine =
        c.fallback_to_pool_after_s != null &&
        anchorMine != null &&
        (nowMs - anchorMine) / 1000 >= c.fallback_to_pool_after_s
      return expiredMine ? "reservedToMeExpired" : "reservedToMe"
    }
    // reservado a outro: só aparece se transbordou (janela expirada).
    if (c.fallback_to_pool_after_s == null) return "reservedOther"  // reserva permanente
    const anchor = c.assigned_at_ms ?? c.first_queued_ms ?? c.queued_at_ms
    if (anchor == null) return "reservedOther"
    const ageS = (nowMs - anchor) / 1000
    return ageS >= c.fallback_to_pool_after_s ? "overflowed" : "reservedOther"
  }, [myUserId, nowMs])

  const handlePull = useCallback(async (c: QueueContact) => {
    setClaiming(c.session_id)
    try {
      const res = await apiFetch(`/api/work_queue/claim/${encodeURIComponent(c.session_id)}`, {
        method:  "POST",
        headers: { "content-type": "application/json" },
        body:    JSON.stringify({
          pool_id:       c.pool_id,
          instance_id:   instanceId,
          // Bug B fix: forward the conference the caller opened so the routed
          // event attaches the human as the conference participant.
          conference_id: c.conference_id ?? "",
        }),
      })
      const result = await res.json() as { claimed?: boolean; reason?: string }
      if (result.claimed) {
        setClaimError(null)
        setContacts(prev => prev.filter(x => x.session_id !== c.session_id))
        onClaimed?.(c.session_id)
      } else {
        // Ordem importa: o refresh vem ANTES, porque ele mexe em `error` (o da
        // listagem). A recusa é gravada depois, no estado dela, e nada a limpa.
        await refresh()
        setClaimError({
          sessionId: c.session_id,
          text: t(`pullInbox.claimReason.${result.reason ?? "failed"}`, { defaultValue: result.reason ?? "" }),
        })
      }
    } catch (e) {
      setClaimError({ sessionId: c.session_id, text: String(e) })
    } finally {
      setClaiming(null)
    }
  }, [instanceId, onClaimed, refresh, t])

  // A recusa deixa de valer quando o ITEM sai da fila — foi reivindicado por outro,
  // expirou, ou o trabalho acabou. Aí a mensagem viraria afirmação sobre algo que
  // não está mais na tela. Recarregar a lista, por si só, não a invalida.
  useEffect(() => {
    if (!claimError) return
    if (!contacts.some(c => c.session_id === claimError.sessionId)) setClaimError(null)
  }, [contacts, claimError])

  // ── Wrap-up unificado (Camada E2) — auto-atendimento (inline) ───────────────
  // Item reservado a MIM e marcado auto_attend (o hook era `dispatch: inline`) é
  // reivindicado AUTOMATICAMENTE, sem o agente clicar "Pull" — o form renderiza na
  // hora (entrega inline). Tentativa ÚNICA por sessão: se falhar (sem vaga na janela
  // do handoff / claim de corrida), o item fica na inbox para o pull manual
  // (degradação graciosa). Não afeta itens detached (auto_attend ausente/false).
  const autoAttendedRef = useRef<Set<string>>(new Set())
  useEffect(() => {
    if (claiming) return
    const target = contacts.find(
      c => c.auto_attend === true
        // Aceita a reserva EXPIRADA também: o item continua sendo o wrap-up deste
        // agente, e depois da janela ele não perde o direito de reivindicar — perde
        // a exclusividade. Restringir a `reservedToMe` faria o auto-atendimento
        // parar de funcionar justamente no caso em que o agente demorou a voltar.
        && (reservationOf(c) === "reservedToMe" || reservationOf(c) === "reservedToMeExpired")
        && !autoAttendedRef.current.has(c.session_id),
    )
    if (target) {
      autoAttendedRef.current.add(target.session_id)
      void handlePull(target)
    }
  }, [contacts, claiming, reservationOf, handlePull])

  if (pullPools.length === 0) return null

  // Agrupa por pool (na ordem de pullPools). Camada B: esconde itens reservados
  // a OUTRO recurso enquanto na janela (reservedOther).
  //
  // ORDENAÇÃO (revista 2026-08-04). A regra geral é UMA — espera real, que o
  // re-enfileiramento preserva em `first_queued_ms` justamente para não mentir.
  // Exceção só vale se ganhar o lugar:
  //
  //   · `reservedToMe` (dentro da janela) GANHA: o item é exclusivamente deste
  //     agente e ninguém mais pode pegá-lo, então pôr no topo não fura fila —
  //     mostra o que só ele resolve.
  //   · `reservedToMeExpired` / `overflowed` NÃO ganham mais: a exclusividade
  //     acabou e o item concorre em igualdade. Mantê-los no topo contradizia o
  //     mesmo fato que lhes tirou o crachá, e cobrava a espera de quem esperava
  //     mais.
  //
  // E o desempate passou a ser o `ageOf` EXIBIDO. Antes ordenava por
  // `queued_at_ms` (que RESETA na devolução à fila) enquanto a tela mostrava a
  // idade derivada de `first_queued_ms` (que não reseta): a lista parecia
  // ordenada pelo número que exibia e não estava — item mostrando-se mais velho
  // aparecia abaixo de um mais novo. Ordenar pelo valor mostrado é o que torna a
  // ordem CONFERÍVEL a olho.
  const rank = (r: string) => (r === "reservedToMe" ? 0 : 1)
  const groups = pullPools
    .map(pid => ({
      pool_id: pid,
      items: contacts
        .filter(c => c.pool_id === pid)
        .filter(c => reservationOf(c) !== "reservedOther")
        .sort((a, b) => {
          const dr = rank(reservationOf(a)) - rank(reservationOf(b))
          if (dr !== 0) return dr
          return (ageOf(b) ?? 0) - (ageOf(a) ?? 0)   // mais velho primeiro
        }),
    }))
    .filter(g => g.items.length > 0)

  return (
    <div className="border-t border-gray-200 mt-2 pt-2">
      <div className="flex items-center justify-between px-3 mb-1">
        <span className="text-xs font-semibold text-dark uppercase tracking-wide">
          {t("pullInbox.title", { defaultValue: "Filas (pull)" })}
        </span>
        <span className="text-xs text-muted-light">{contacts.length}</span>
      </div>
      {error && (
        <div className="px-3 text-xs text-warning-text mb-1">{error}</div>
      )}
      {claimError && (
        <div className="mx-2 mb-1 flex items-start gap-2 rounded border border-warning/40
                        bg-warning-light px-2 py-1.5 text-2xs text-warning-text">
          <span className="flex-1">
            {t("pullInbox.claimFailed", {
              id: claimError.sessionId.slice(0, 8),
              reason: claimError.text,
              defaultValue: "Não foi possível atender {{id}}: {{reason}}",
            })}
          </span>
          <button
            type="button"
            onClick={() => setClaimError(null)}
            className="flex-shrink-0 font-semibold hover:underline"
          >
            {t("pullInbox.claimFailedDismiss", { defaultValue: "OK" })}
          </button>
        </div>
      )}
      {groups.length === 0 ? (
        <div className="px-3 py-2 text-xs text-muted-light">
          {t("pullInbox.empty", { defaultValue: "Nenhum contato aguardando." })}
        </div>
      ) : (
        <div className="space-y-0.5">
          {groups.map(g => {
            const isCollapsed = collapsed.has(g.pool_id)
            return (
              <div key={g.pool_id}>
                {/* Cabeçalho da fila (pool) — recolhível */}
                <button
                  type="button"
                  onClick={() => toggleGroup(g.pool_id)}
                  className="w-full flex items-center gap-1 px-2 py-1 text-2xs font-semibold text-muted uppercase tracking-wide hover:bg-gray-50"
                >
                  <ChevronRight
                    className={`w-3 h-3 flex-shrink-0 transition-transform ${isCollapsed ? "" : "rotate-90"}`}
                    aria-hidden="true"
                  />
                  <span className="truncate flex-1 text-left">{queueLabel(g.pool_id, t)}</span>
                  <span className="text-muted-light">({g.items.length})</span>
                </button>
                {!isCollapsed && (
                  <ul className="space-y-1 px-2 pb-1">
                    {g.items.map(c => {
                      const age = ageOf(c)
                      const reservation = reservationOf(c)
                      return (
                        <li
                          key={c.session_id}
                          className={`flex items-center justify-between gap-2 rounded px-2 py-1.5 ${
                            previewSessionId === c.session_id ? "bg-primary-light" : "hover:bg-gray-50"
                          }`}
                        >
                          <button
                            type="button"
                            onClick={() => onPreview?.(c.session_id, c.pool_id, c.conference_id ?? "")}
                            className="min-w-0 flex-1 text-left"
                            title={t("pullInbox.previewHint", { defaultValue: "Ver contexto antes de atender" })}
                          >
                            <div className="flex items-center gap-1.5 min-w-0">
                              <span className="text-sm text-dark truncate">
                                {c.summary ?? c.session_id.slice(0, 8)}
                              </span>
                              {reservation === "reservedToMe" && (
                                <span className="flex-shrink-0 text-2xs font-semibold uppercase tracking-wide px-1 py-0.5 rounded bg-primary-light text-primary">
                                  {t("pullInbox.reservedToYou", { defaultValue: "Reservado a você" })}
                                </span>
                              )}
                              {/* `reservedToMeExpired` e `overflowed` NÃO têm crachá, de propósito
                                  (2026-08-04). Os dois significam "qualquer um do pool pode pegar
                                  agora", que é exatamente o que `shared` significa — e `shared` é
                                  renderizado SEM selo. A ausência já é a notação; um selo dizendo
                                  o default só compete com o único que carrega informação
                                  ("Reservado a você"). Os nomes anteriores ("Reserva expirada" /
                                  "Transbordado") descreviam o que acontecera com a RESERVA e eram
                                  lidos como "o item morreu" — levaram a pedir a remoção da fila de
                                  itens perfeitamente válidos. O estado segue vivo no
                                  `reservationOf` (filtro e ordenação); só a marca visual saiu. */}
                            </div>
                            {age != null && (
                              <div className="text-xs truncate">
                                <span className={slaColor(age, poolSla?.[c.pool_id])}>{fmtAge(age)}</span>
                              </div>
                            )}
                          </button>
                        </li>
                      )
                    })}
                  </ul>
                )}
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}

export default PullInboxPanel
