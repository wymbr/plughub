/**
 * PullInboxPanel — Frente 1 F2b-2a: inbox mínima do dispatch pull.
 *
 * Lista os contatos CLAIMÁVEIS das filas pull em que o agente está logado
 * (poll de GET /api/work_queue/list) e oferece o botão "Pull" por contato
 * (POST /api/work_queue/claim/:sessionId). No sucesso, o contato anexa pelo
 * fluxo existente (conversation.assigned via WS) → vira atendimento normal; o
 * onClaimed permite selecioná-lo na lista de contatos.
 *
 * Self-contained: recebe os pools pull, o instance_id do agente e callbacks por
 * props. O polish (rail 3-zonas, cor SLA, preview, heartbeat) é a F2b-2b.
 */
import React, { useCallback, useEffect, useState } from "react"
import { useTranslation } from "react-i18next"

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
  /** Chamado após um claim bem-sucedido (sessionId) — ex.: selecionar na lista. */
  onClaimed?:   (sessionId: string) => void
  /** F2b-2b — clicar na linha abre o preview read-only (sem claim). */
  onPreview?:   (sessionId: string, poolId: string) => void
  /** Sessão em preview no momento (highlight da linha). */
  previewSessionId?: string | null
  /** Intervalo de polling em ms (default 4000). */
  pollMs?:      number
}

function fmtAge(ms: number | null): string {
  if (ms == null) return ""
  const s = Math.floor(ms / 1000)
  if (s < 60) return `${s}s`
  const m = Math.floor(s / 60)
  return `${m}m ${s % 60}s`
}

export const PullInboxPanel: React.FC<PullInboxPanelProps> = ({
  pullPools, instanceId, onClaimed, onPreview, previewSessionId, pollMs = 4000,
}) => {
  const { t } = useTranslation("agentAssist")
  const [contacts, setContacts] = useState<QueueContact[]>([])
  const [claiming, setClaiming] = useState<string | null>(null)
  const [error, setError]       = useState<string | null>(null)

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
        // Sai da lista imediatamente; o WS (conversation.assigned) anexa o contato.
        setContacts(prev => prev.filter(x => x.session_id !== c.session_id))
        onClaimed?.(c.session_id)
      } else {
        // Perdeu o claim (outro agente) / sem capacidade → some da lista e re-busca.
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
      {contacts.length === 0 ? (
        <div className="px-3 py-2 text-xs text-muted-light">
          {t("pullInbox.empty", { defaultValue: "Nenhum contato aguardando." })}
        </div>
      ) : (
        <ul className="space-y-1 px-2">
          {contacts.map(c => (
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
                <div className="text-xs text-muted-light truncate">
                  {c.pool_id}{c.age_ms != null ? ` · ${fmtAge(c.age_ms)}` : ""}
                </div>
              </button>
              <button
                type="button"
                disabled={claiming === c.session_id}
                onClick={() => handlePull(c)}
                className="shrink-0 rounded bg-primary px-2.5 py-1 text-xs font-medium text-white disabled:opacity-50"
              >
                {claiming === c.session_id
                  ? t("pullInbox.pulling", { defaultValue: "..." })
                  : t("pullInbox.pull", { defaultValue: "Pull" })}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

export default PullInboxPanel
