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

interface QueueContact {
  session_id:   string
  pool_id:      string
  state:        string
  channel:      string | null
  summary:      string | null
  queued_at_ms: number | null
  age_ms:       number | null
}

interface PullInboxPanelProps {
  /** Pools pull em que o agente está logado (accessible ∩ dispatch_mode=pull). */
  pullPools:    string[]
  /** Instância do agente para o claim (ex.: human-{userId}). */
  instanceId:   string
  /** SLA target (ms) por pool — para a cor de urgência das linhas. */
  poolSla?:     Record<string, number | null>
  /** Bloqueia o claim (teto de sessões simultâneas atingido). */
  claimDisabled?:       boolean
  /** Hint exibido no botão quando claimDisabled. */
  claimDisabledReason?: string
  /** Chamado após um claim bem-sucedido (sessionId) — ex.: selecionar na lista. */
  onClaimed?:   (sessionId: string) => void
  /** F2b-2b — clicar na linha abre o preview read-only (sem claim). */
  onPreview?:   (sessionId: string, poolId: string) => void
  /** Sessão em preview no momento (highlight da linha). */
  previewSessionId?: string | null
  /** F2b-2b-2 — a sessão em preview saiu da fila (claim de outro / timeout). */
  onPreviewInvalid?: () => void
  /** Intervalo de polling em ms (default 4000). */
  pollMs?:      number
}

function fmtAge(ms: number | null): string {
  if (ms == null) return ""
  const s = Math.max(0, Math.floor(ms / 1000))
  if (s < 60) return `${s}s`
  const m = Math.floor(s / 60)
  return `${m}m ${s % 60}s`
}

// Cor por urgência: idade relativa ao SLA do pool. Sem SLA → neutro.
function slaColor(ageMs: number | null, slaMs: number | null | undefined): string {
  if (ageMs == null || !slaMs || slaMs <= 0) return "text-muted-light"
  const r = ageMs / slaMs
  if (r >= 1)   return "text-red-text"
  if (r >= 0.6) return "text-warning-text"
  return "text-green-text"
}

export const PullInboxPanel: React.FC<PullInboxPanelProps> = ({
  pullPools, instanceId, poolSla, claimDisabled, claimDisabledReason,
  onClaimed, onPreview, previewSessionId, onPreviewInvalid, pollMs = 4000,
}) => {
  const { t } = useTranslation("agentAssist")
  const [contacts, setContacts] = useState<QueueContact[]>([])
  const [claiming, setClaiming] = useState<string | null>(null)
  const [error, setError]       = useState<string | null>(null)
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
      const res = await fetch(url)
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
    if (c.queued_at_ms != null) return Math.max(0, nowMs - c.queued_at_ms)
    return c.age_ms
  }, [nowMs])

  const handlePull = useCallback(async (c: QueueContact) => {
    setClaiming(c.session_id)
    try {
      const res = await fetch(`/api/work_queue/claim/${encodeURIComponent(c.session_id)}`, {
        method:  "POST",
        headers: { "content-type": "application/json" },
        body:    JSON.stringify({ pool_id: c.pool_id, instance_id: instanceId }),
      })
      const result = await res.json() as { claimed?: boolean; reason?: string }
      if (result.claimed) {
        setContacts(prev => prev.filter(x => x.session_id !== c.session_id))
        onClaimed?.(c.session_id)
      } else {
        setError(t(`pullInbox.claimReason.${result.reason ?? "failed"}`, { defaultValue: result.reason ?? "" }))
        await refresh()
      }
    } catch (e) {
      setError(String(e))
    } finally {
      setClaiming(null)
    }
  }, [instanceId, onClaimed, refresh, t])

  if (pullPools.length === 0) return null

  // Agrupa por pool (na ordem de pullPools); dentro do grupo, mais-antigo-primeiro.
  const groups = pullPools
    .map(pid => ({
      pool_id: pid,
      items: contacts
        .filter(c => c.pool_id === pid)
        .sort((a, b) => (a.queued_at_ms ?? 0) - (b.queued_at_ms ?? 0)),
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
                  <span className="truncate flex-1 text-left">{g.pool_id}</span>
                  <span className="text-muted-light">({g.items.length})</span>
                </button>
                {!isCollapsed && (
                  <ul className="space-y-1 px-2 pb-1">
                    {g.items.map(c => {
                      const age = ageOf(c)
                      return (
                        <li
                          key={c.session_id}
                          className={`flex items-center justify-between gap-2 rounded px-2 py-1.5 ${
                            previewSessionId === c.session_id ? "bg-primary-light" : "hover:bg-gray-50"
                          }`}
                        >
                          <button
                            type="button"
                            onClick={() => onPreview?.(c.session_id, c.pool_id)}
                            className="min-w-0 flex-1 text-left"
                            title={t("pullInbox.previewHint", { defaultValue: "Ver contexto antes de atender" })}
                          >
                            <div className="text-sm text-dark truncate">
                              {c.summary ?? c.session_id.slice(0, 8)}
                            </div>
                            {age != null && (
                              <div className="text-xs truncate">
                                <span className={slaColor(age, poolSla?.[c.pool_id])}>{fmtAge(age)}</span>
                              </div>
                            )}
                          </button>
                          <button
                            type="button"
                            disabled={claiming === c.session_id || claimDisabled}
                            onClick={() => handlePull(c)}
                            title={claimDisabled ? claimDisabledReason : undefined}
                            className="shrink-0 rounded bg-primary px-2.5 py-1 text-xs font-medium text-white disabled:opacity-50"
                          >
                            {claiming === c.session_id
                              ? t("pullInbox.pulling", { defaultValue: "..." })
                              : t("pullInbox.pull", { defaultValue: "Pull" })}
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
