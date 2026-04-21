/**
 * SupervisorPanel.tsx
 * Inline intervention panel attached to the session transcript.
 *
 * Shown only when the operator has joined as supervisor (state.status === 'active').
 * Provides:
 *   - Message composer with visibility toggle (agents_only | all)
 *   - Send button (Enter key support)
 *   - Leave button
 *   - Error display
 *
 * Visibility semantics:
 *   agents_only → coaching message, customer never sees it (default)
 *   all         → direct intervention, everyone sees it
 */
import { useRef, useState } from 'react'
import type React from 'react'
import type { SupervisorState } from '../types'

interface Props {
  state:      SupervisorState
  onMessage:  (text: string, visibility: 'agents_only' | 'all') => Promise<void>
  onLeave:    () => Promise<void>
}

export function SupervisorPanel({ state, onMessage, onLeave }: Props) {
  const [text,       setText]       = useState('')
  const [visibility, setVisibility] = useState<'agents_only' | 'all'>('agents_only')
  const [sending,    setSending]    = useState(false)
  const textareaRef = useRef<HTMLTextAreaElement>(null)

  async function handleSend() {
    const trimmed = text.trim()
    if (!trimmed || sending) return
    setSending(true)
    try {
      await onMessage(trimmed, visibility)
      setText('')
      textareaRef.current?.focus()
    } finally {
      setSending(false)
    }
  }

  function handleKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSend()
    }
  }

  const visColor  = visibility === 'agents_only' ? '#22c55e' : '#f59e0b'
  const visLabel  = visibility === 'agents_only' ? 'agents only' : 'all participants'
  const visToggle = visibility === 'agents_only' ? 'all' : 'agents_only'

  return (
    <div style={styles.panel}>
      {/* Status bar */}
      <div style={styles.statusBar}>
        <span style={styles.activeDot} />
        <span style={styles.statusText}>Supervisor ativo</span>
        <span style={styles.joinedAt}>
          {state.joinedAt ? `desde ${formatTime(state.joinedAt)}` : ''}
        </span>
        <button
          style={styles.leaveBtn}
          onClick={onLeave}
          disabled={state.status === 'leaving'}
          title="Sair da sessão como supervisor"
        >
          {state.status === 'leaving' ? 'Saindo…' : 'Sair'}
        </button>
      </div>

      {/* Error display */}
      {state.error && (
        <div style={styles.errorBanner}>⚠ {state.error}</div>
      )}

      {/* Composer */}
      <div style={styles.composer}>
        <textarea
          ref           ={textareaRef}
          value         ={text}
          onChange      ={e => setText(e.target.value)}
          onKeyDown     ={handleKeyDown}
          placeholder   ="Digite uma mensagem de supervisão… (Enter para enviar)"
          style         ={styles.textarea}
          rows          ={2}
          disabled      ={sending}
          autoFocus
        />
        <div style={styles.composerFooter}>
          {/* Visibility toggle */}
          <button
            style={{ ...styles.visBtn, color: visColor, borderColor: visColor + '44' }}
            onClick={() => setVisibility(visToggle as 'agents_only' | 'all')}
            title="Alternar visibilidade da mensagem"
          >
            <span style={{ ...styles.visDot, backgroundColor: visColor }} />
            {visLabel}
          </button>

          <span style={styles.hint}>Shift+Enter para nova linha</span>

          <button
            style={{ ...styles.sendBtn, opacity: (!text.trim() || sending) ? 0.4 : 1 }}
            onClick={handleSend}
            disabled={!text.trim() || sending}
          >
            {sending ? 'Enviando…' : 'Enviar'}
          </button>
        </div>
      </div>
    </div>
  )
}

// ─── Join button (shown when idle) ────────────────────────────────────────────

interface JoinProps {
  onJoin:   () => void
  joining:  boolean
  error:    string | null
}

export function SupervisorJoinButton({ onJoin, joining, error }: JoinProps) {
  return (
    <div style={styles.joinArea}>
      {error && <span style={styles.joinError}>{error}</span>}
      <button
        style={{ ...styles.joinBtn, opacity: joining ? 0.6 : 1 }}
        onClick={onJoin}
        disabled={joining}
        title="Entrar como supervisor nesta sessão"
      >
        {joining ? 'Entrando…' : '⌥ Entrar como supervisor'}
      </button>
    </div>
  )
}

// ─── Helpers ──────────────────────────────────────────────────────────────────

function formatTime(iso: string): string {
  try {
    return new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
  } catch {
    return iso
  }
}

// ─── Styles ───────────────────────────────────────────────────────────────────

const styles: Record<string, React.CSSProperties> = {
  panel: {
    borderTop:       '1px solid #1e293b',
    backgroundColor: '#0a1628',
    flexShrink:      0,
  },
  statusBar: {
    display:         'flex',
    alignItems:      'center',
    gap:             8,
    padding:         '8px 16px',
    backgroundColor: '#0f1f0f',
    borderBottom:    '1px solid #1e3a1e',
  },
  activeDot: {
    display:         'inline-block',
    width:           8,
    height:          8,
    borderRadius:    '50%',
    backgroundColor: '#22c55e',
    boxShadow:       '0 0 0 3px #22c55e33',
    flexShrink:      0,
  },
  statusText: {
    fontSize:    13,
    fontWeight:  600,
    color:       '#22c55e',
  },
  joinedAt: {
    fontSize: 11,
    color:    '#475569',
    flex:     1,
  },
  leaveBtn: {
    background:   'none',
    border:       '1px solid #ef444466',
    color:        '#ef4444',
    borderRadius: 6,
    padding:      '3px 10px',
    cursor:       'pointer',
    fontSize:     12,
  },
  errorBanner: {
    padding:         '6px 16px',
    backgroundColor: '#3f0e0e',
    color:           '#fca5a5',
    fontSize:        12,
  },
  composer: {
    padding: '10px 16px 12px',
  },
  textarea: {
    width:           '100%',
    background:      '#0f172a',
    border:          '1px solid #334155',
    borderRadius:    6,
    color:           '#e2e8f0',
    fontSize:        13,
    padding:         '8px 10px',
    resize:          'none',
    outline:         'none',
    boxSizing:       'border-box',
    lineHeight:      1.5,
    fontFamily:      'inherit',
  },
  composerFooter: {
    display:    'flex',
    alignItems: 'center',
    gap:        10,
    marginTop:  6,
  },
  visBtn: {
    display:      'flex',
    alignItems:   'center',
    gap:          5,
    background:   'none',
    border:       '1px solid',
    borderRadius: 6,
    padding:      '3px 8px',
    cursor:       'pointer',
    fontSize:     11,
    fontWeight:   600,
    letterSpacing: '0.04em',
    textTransform: 'uppercase',
  },
  visDot: {
    display:      'inline-block',
    width:        6,
    height:       6,
    borderRadius: '50%',
  },
  hint: {
    flex:     1,
    fontSize: 10,
    color:    '#475569',
  },
  sendBtn: {
    background:   '#1e40af',
    border:       'none',
    color:        '#e2e8f0',
    borderRadius: 6,
    padding:      '5px 16px',
    cursor:       'pointer',
    fontSize:     13,
    fontWeight:   600,
  },
  joinArea: {
    display:    'flex',
    alignItems: 'center',
    gap:        10,
  },
  joinBtn: {
    background:   'none',
    border:       '1px solid #f59e0b66',
    color:        '#f59e0b',
    borderRadius: 6,
    padding:      '4px 12px',
    cursor:       'pointer',
    fontSize:     12,
    fontWeight:   600,
    whiteSpace:   'nowrap',
  },
  joinError: {
    fontSize: 11,
    color:    '#fca5a5',
  },
}
