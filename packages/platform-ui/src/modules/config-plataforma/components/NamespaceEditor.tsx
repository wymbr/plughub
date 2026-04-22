/**
 * NamespaceEditor.tsx
 * Editor para os 8 namespaces de configuração da plataforma (config-api).
 *
 * Left: namespace selector
 * Right: table of key/value with inline JSON edit
 */
import React, { useState } from 'react'
import { useNamespace, putConfig } from '../api/config-hooks'
import { useTranslation } from 'react-i18next'

const NAMESPACES = [
  { id: 'sentiment',  label: 'Sentimento',     icon: '💬', desc: 'Thresholds e TTL do sentimento no AI Gateway' },
  { id: 'routing',    label: 'Roteamento',     icon: '🔀', desc: 'SLA, snapshots e pesos do algoritmo de roteamento' },
  { id: 'session',    label: 'Sessão',         icon: '⏱',  desc: 'TTLs de sessão por componente' },
  { id: 'consumer',   label: 'Consumer',       icon: '📥', desc: 'Parâmetros do Kafka consumer da analytics-api' },
  { id: 'dashboard',  label: 'Dashboard',      icon: '📊', desc: 'Intervalo SSE e retry do dashboard operacional' },
  { id: 'webchat',    label: 'WebChat',        icon: '💻', desc: 'Auth timeout, expiração de attachments, limites de upload' },
  { id: 'masking',    label: 'Mascaramento',   icon: '🔒', desc: 'Política de acesso ao original_content e audit capture' },
  { id: 'quota',      label: 'Quotas',         icon: '📏', desc: 'Limites operacionais de sessões, tokens e mensagens' },
]

interface Props {
  tenantId:   string
  adminToken: string
}

export function NamespaceEditor({ tenantId, adminToken }: Props) {
  const [selectedNs, setSelectedNs]   = useState(NAMESPACES[0].id)
  const [editKey,    setEditKey]       = useState<string | null>(null)
  const [editValue,  setEditValue]     = useState('')
  const [saveError,  setSaveError]     = useState<string | null>(null)
  const [saving,     setSaving]        = useState(false)

  const { entries, loading, error, reload } = useNamespace(tenantId, selectedNs)

  const ns = NAMESPACES.find(n => n.id === selectedNs)!

  function openEdit(key: string) {
    setEditKey(key)
    setEditValue(JSON.stringify(entries[key], null, 2))
    setSaveError(null)
  }

  function cancelEdit() {
    setEditKey(null)
    setSaveError(null)
  }

  async function handleSave() {
    if (!editKey) return
    let parsed: unknown
    try { parsed = JSON.parse(editValue) }
    catch { setSaveError('JSON inválido'); return }

    setSaving(true)
    setSaveError(null)
    try {
      await putConfig(selectedNs, editKey, parsed, tenantId, adminToken)
      setEditKey(null)
      reload()
    } catch (e) {
      setSaveError(String(e))
    } finally {
      setSaving(false)
    }
  }

  return (
    <div style={{ display: 'flex', gap: 0, height: '100%' }}>
      {/* Namespace sidebar */}
      <div style={sideStyle}>
        {NAMESPACES.map(n => (
          <button
            key={n.id}
            style={{
              ...nsItemStyle,
              background: n.id === selectedNs ? '#1e40af22' : 'none',
              borderLeft: n.id === selectedNs ? '3px solid #3b82f6' : '3px solid transparent',
              color: n.id === selectedNs ? '#93c5fd' : '#94a3b8',
            }}
            onClick={() => { setSelectedNs(n.id); setEditKey(null) }}
          >
            <span style={{ fontSize: 16 }}>{n.icon}</span>
            <span style={{ fontWeight: n.id === selectedNs ? 600 : 400 }}>{n.label}</span>
          </button>
        ))}
      </div>

      {/* Key-value table */}
      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
        {/* Namespace header */}
        <div style={nsHeaderStyle}>
          <div>
            <span style={{ fontSize: 16 }}>{ns.icon}</span>
            <span style={{ marginLeft: 8, fontWeight: 600, fontSize: 15, color: '#e2e8f0' }}>{ns.label}</span>
            <span style={{ marginLeft: 8, fontSize: 12, color: '#64748b' }}>{ns.desc}</span>
          </div>
          {loading && <span style={{ fontSize: 12, color: '#64748b' }}>⟳ carregando…</span>}
          {error && <span style={{ fontSize: 12, color: '#ef4444' }}>⚠ {error}</span>}
        </div>

        {/* Entries */}
        <div style={{ flex: 1, overflowY: 'auto' }}>
          {Object.entries(entries).map(([key, value]) => (
            <div key={key} style={rowStyle}>
              <div style={{ flex: '0 0 220px', fontSize: 13, fontWeight: 600, color: '#93c5fd', fontFamily: 'monospace' }}>
                {key}
              </div>

              {editKey === key ? (
                <div style={{ flex: 1, display: 'flex', flexDirection: 'column', gap: 8 }}>
                  <textarea
                    value={editValue}
                    onChange={e => setEditValue(e.target.value)}
                    style={textareaStyle}
                    rows={Math.min(10, editValue.split('\n').length + 1)}
                    spellCheck={false}
                  />
                  {saveError && <span style={{ fontSize: 12, color: '#ef4444' }}>⚠ {saveError}</span>}
                  <div style={{ display: 'flex', gap: 8 }}>
                    <button style={btnPrimary} onClick={handleSave} disabled={saving}>
                      {saving ? 'Salvando…' : 'Salvar'}
                    </button>
                    <button style={btnSecondary} onClick={cancelEdit}>Cancelar</button>
                  </div>
                </div>
              ) : (
                <div style={{ flex: 1, display: 'flex', alignItems: 'flex-start', gap: 12 }}>
                  <pre style={valueStyle}>{JSON.stringify(value, null, 2)}</pre>
                  {adminToken && (
                    <button style={{ ...btnSecondary, flexShrink: 0 }} onClick={() => openEdit(key)}>
                      ✏ Editar
                    </button>
                  )}
                </div>
              )}
            </div>
          ))}

          {!loading && Object.keys(entries).length === 0 && !error && (
            <div style={{ padding: '40px 24px', color: '#475569', textAlign: 'center', fontSize: 14 }}>
              Nenhuma configuração encontrada neste namespace para o tenant <code>{tenantId}</code>.
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

const sideStyle: React.CSSProperties = {
  width: 180, flexShrink: 0, borderRight: '1px solid #1e293b',
  display: 'flex', flexDirection: 'column', padding: '8px 0', overflowY: 'auto',
  backgroundColor: '#0f172a',
}

const nsItemStyle: React.CSSProperties = {
  display: 'flex', alignItems: 'center', gap: 10,
  padding: '10px 16px',
  cursor: 'pointer', border: 'none', width: '100%', textAlign: 'left',
  fontSize: 13, transition: 'background 0.1s',
}

const nsHeaderStyle: React.CSSProperties = {
  display: 'flex', alignItems: 'center', justifyContent: 'space-between',
  padding: '14px 20px', borderBottom: '1px solid #1e293b',
  backgroundColor: '#0a1628', flexShrink: 0,
}

const rowStyle: React.CSSProperties = {
  display: 'flex', alignItems: 'flex-start', gap: 16,
  padding: '14px 20px', borderBottom: '1px solid #1e293b',
}

const valueStyle: React.CSSProperties = {
  flex: 1, margin: 0, fontSize: 12, color: '#94a3b8',
  fontFamily: 'monospace', whiteSpace: 'pre-wrap', wordBreak: 'break-all',
  maxHeight: 120, overflow: 'auto',
  backgroundColor: '#0f172a', borderRadius: 6, padding: '6px 10px',
}

const textareaStyle: React.CSSProperties = {
  width: '100%', background: '#0f172a', border: '1px solid #334155',
  borderRadius: 6, color: '#e2e8f0', fontSize: 12, padding: '8px 10px',
  resize: 'vertical', outline: 'none', boxSizing: 'border-box',
  lineHeight: 1.5, fontFamily: 'monospace',
}

const btnPrimary: React.CSSProperties = {
  background: '#1e40af', border: 'none', color: '#e2e8f0',
  borderRadius: 6, padding: '5px 14px', cursor: 'pointer', fontSize: 13, fontWeight: 600,
}

const btnSecondary: React.CSSProperties = {
  background: 'none', border: '1px solid #334155', color: '#94a3b8',
  borderRadius: 6, padding: '4px 12px', cursor: 'pointer', fontSize: 12,
}
