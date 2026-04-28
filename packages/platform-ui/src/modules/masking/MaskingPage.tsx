/**
 * MaskingPage — /config/masking
 *
 * Dedicated UI for message masking configuration.
 * Settings are stored in Config API (namespace "masking") and read by
 * MaskingService in mcp-server-plughub via Redis cache fallback chain.
 *
 * Sections:
 *   1. Access Policy — who can see original_content (unmasked values)
 *   2. Audit Capture — whether to capture input/output in audit records
 *   3. Retention     — how long masked tokens are kept in Redis
 *   4. Rules overview — read-only list of DEFAULT_MASKING_RULES categories
 */
import React, { useState } from 'react'
import { useAuth } from '@/auth/useAuth'
import { useNamespace, putConfig } from '../config-plataforma/api/config-hooks'

// ── Masking categories (mirrors DEFAULT_MASKING_RULES in schemas/audit.ts) ─────
const DEFAULT_CATEGORIES = [
  { id: 'credit_card',  label: 'Cartão de Crédito', example: '****1234',      icon: '💳' },
  { id: 'cpf',          label: 'CPF',                example: '***-00',        icon: '🪪' },
  { id: 'phone',        label: 'Telefone',           example: '(11) ****-4321', icon: '📞' },
  { id: 'email_addr',   label: 'E-mail',             example: 'j***@emp.com',  icon: '📧' },
  { id: 'iban',         label: 'IBAN',               example: 'BR***1234',     icon: '🏦' },
  { id: 'passport',     label: 'Passaporte',         example: '***1234',       icon: '🛂' },
]

const ROLES_OPTIONS = ['evaluator', 'reviewer', 'supervisor', 'admin', 'developer']

// ── Helpers ──────────────────────────────────────────────────────────────────

function badge(text: string, color: string) {
  return (
    <span style={{
      display: 'inline-block', padding: '2px 8px', borderRadius: 12,
      fontSize: 11, fontWeight: 600, background: color + '22', color,
      border: `1px solid ${color}44`,
    }}>{text}</span>
  )
}

// ── Main component ────────────────────────────────────────────────────────────

export default function MaskingPage() {
  const { session }    = useAuth()
  const tenantId       = session?.tenantId ?? ''
  const [adminToken,   setAdminToken]   = useState('')
  const [showToken,    setShowToken]    = useState(false)
  const [saving,       setSaving]       = useState<string | null>(null)
  const [toast,        setToast]        = useState<{ msg: string; ok: boolean } | null>(null)

  const { entries, loading, error, reload } = useNamespace(tenantId, 'masking')

  // Resolved values with defaults — entries[key].value holds the actual config value
  const val = (key: string): unknown => entries[key]?.value ?? entries[key]

  const authorizedRoles: string[] = Array.isArray(val('authorized_roles'))
    ? (val('authorized_roles') as string[])
    : ['evaluator', 'reviewer']

  const captureInput:     boolean = val('capture_input_default')  === true
  const captureOutput:    boolean = val('capture_output_default') === true
  const retentionDays:    number  = typeof val('default_retention_days') === 'number'
    ? (val('default_retention_days') as number)
    : 30

  function showToast(msg: string, ok: boolean) {
    setToast({ msg, ok })
    setTimeout(() => setToast(null), 3000)
  }

  async function saveKey(key: string, value: unknown) {
    if (!adminToken) { showToast('Admin token obrigatório para salvar', false); return }
    setSaving(key)
    try {
      await putConfig('masking', key, value, tenantId, adminToken)
      reload()
      showToast(`${key} salvo`, true)
    } catch (e) {
      showToast(String(e), false)
    } finally {
      setSaving(null)
    }
  }

  function toggleRole(role: string) {
    const next = authorizedRoles.includes(role)
      ? authorizedRoles.filter(r => r !== role)
      : [...authorizedRoles, role]
    saveKey('authorized_roles', next)
  }

  return (
    <div style={{ height: '100%', overflowY: 'auto', background: '#0a1628', color: '#e2e8f0' }}>
      {/* Toast */}
      {toast && (
        <div style={{
          position: 'fixed', top: 16, right: 24, zIndex: 1000,
          background: toast.ok ? '#064e3b' : '#7f1d1d',
          border: `1px solid ${toast.ok ? '#10b981' : '#ef4444'}`,
          borderRadius: 8, padding: '10px 18px', fontSize: 13,
          color: toast.ok ? '#6ee7b7' : '#fca5a5', boxShadow: '0 4px 20px #0008',
        }}>
          {toast.ok ? '✓' : '✗'} {toast.msg}
        </div>
      )}

      {/* Page header */}
      <div style={{ padding: '20px 28px 16px', borderBottom: '1px solid #1e293b' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 16 }}>
          <div>
            <h1 style={{ margin: 0, fontSize: 18, fontWeight: 700, color: '#e2e8f0' }}>
              🔒 Mascaramento de Mensagens
            </h1>
            <p style={{ margin: '4px 0 0', fontSize: 13, color: '#64748b' }}>
              Tokens inline protegem dados sensíveis no stream canônico.
              Alterações propagam em até 60 s (TTL do cache Redis da Config API).
            </p>
          </div>
          {/* Admin token */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexShrink: 0 }}>
            <label style={{ fontSize: 12, color: '#64748b' }}>Admin Token:</label>
            <input
              type={showToken ? 'text' : 'password'}
              value={adminToken}
              onChange={e => setAdminToken(e.target.value)}
              placeholder="Para habilitar edição"
              style={inputStyle}
            />
            <button style={iconBtn} onClick={() => setShowToken(v => !v)}>
              {showToken ? '🙈' : '👁'}
            </button>
            {adminToken && <span style={{ fontSize: 11, color: '#22c55e', fontWeight: 600 }}>✓</span>}
          </div>
        </div>
      </div>

      {/* Loading / error */}
      {loading && <div style={infoBox('#1e293b', '#94a3b8')}>Carregando configurações…</div>}
      {error   && <div style={infoBox('#7f1d1d22', '#fca5a5')}>⚠ {error}</div>}

      <div style={{ padding: '20px 28px', display: 'flex', flexDirection: 'column', gap: 24 }}>

        {/* ── Section 1: Access Policy ─────────────────────────────────────── */}
        <Section
          icon="👥"
          title="Controle de Acesso ao original_content"
          desc="Define quais roles podem ver o valor original de campos mascarados (via session_context_get e evaluation_context_get). Agentes com roles fora desta lista recebem apenas o display_partial (ex: ***-00)."
        >
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginTop: 12 }}>
            {ROLES_OPTIONS.map(role => {
              const active = authorizedRoles.includes(role)
              return (
                <button
                  key={role}
                  onClick={() => toggleRole(role)}
                  disabled={saving === 'authorized_roles'}
                  style={{
                    padding: '6px 16px', borderRadius: 20, fontSize: 13, fontWeight: 500,
                    cursor: 'pointer', transition: 'all 0.15s',
                    background: active ? '#1e3a5f' : '#0f172a',
                    border: active ? '1px solid #3b82f6' : '1px solid #334155',
                    color: active ? '#93c5fd' : '#64748b',
                  }}
                >
                  {active ? '✓ ' : ''}{role}
                </button>
              )
            })}
          </div>
          <p style={{ margin: '10px 0 0', fontSize: 12, color: '#475569' }}>
            Roles ativos: {authorizedRoles.length === 0 ? '(nenhum — todo conteúdo mascarado)' : authorizedRoles.join(', ')}
          </p>
        </Section>

        {/* ── Section 2: Audit Capture ──────────────────────────────────────── */}
        <Section
          icon="📋"
          title="Captura de Audit Records"
          desc="Controla se input/output das tool calls MCP são capturados nos registros de auditoria (Kafka topic mcp.audit). Atenção LGPD: capture_input pode incluir valores mascarados resolvidos."
        >
          <div style={{ display: 'flex', gap: 16, marginTop: 12, flexWrap: 'wrap' }}>
            <ToggleCard
              label="Capturar Input"
              sublabel="capture_input_default"
              active={captureInput}
              onToggle={() => saveKey('capture_input_default', !captureInput)}
              saving={saving === 'capture_input_default'}
              warning="Atenção: pode capturar valores sensíveis resolvidos"
            />
            <ToggleCard
              label="Capturar Output"
              sublabel="capture_output_default"
              active={captureOutput}
              onToggle={() => saveKey('capture_output_default', !captureOutput)}
              saving={saving === 'capture_output_default'}
            />
          </div>
        </Section>

        {/* ── Section 3: Token Retention ────────────────────────────────────── */}
        <Section
          icon="⏱"
          title="Retenção de Tokens"
          desc="Tempo em dias que os tokens de mascaramento são mantidos no Redis. Após expirar, o original_content não pode mais ser resolvido — use apenas em sessões encerradas ou relatórios com dados já exportados."
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginTop: 12 }}>
            <RetentionEditor
              value={retentionDays}
              onSave={v => saveKey('default_retention_days', v)}
              saving={saving === 'default_retention_days'}
            />
          </div>
        </Section>

        {/* ── Section 4: Categories overview ───────────────────────────────── */}
        <Section
          icon="📋"
          title="Categorias de Mascaramento"
          desc="Categorias ativas por padrão (DEFAULT_MASKING_RULES em @plughub/schemas/audit.ts). Para adicionar regras customizadas ou desativar categorias, configure {tenantId}:masking:config diretamente no Redis."
        >
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(260px, 1fr))', gap: 10, marginTop: 12 }}>
            {DEFAULT_CATEGORIES.map(cat => (
              <div key={cat.id} style={{
                background: '#0f172a', border: '1px solid #1e293b', borderRadius: 8,
                padding: '12px 16px', display: 'flex', alignItems: 'flex-start', gap: 12,
              }}>
                <span style={{ fontSize: 22 }}>{cat.icon}</span>
                <div>
                  <div style={{ fontWeight: 600, fontSize: 13, color: '#e2e8f0' }}>{cat.label}</div>
                  <div style={{ fontSize: 11, color: '#64748b', marginTop: 2 }}>
                    Exibe: <code style={{ color: '#94a3b8', background: '#1e293b', padding: '0 4px', borderRadius: 3 }}>{cat.example}</code>
                  </div>
                  <div style={{ marginTop: 6 }}>{badge('ativo', '#22c55e')}</div>
                </div>
              </div>
            ))}
          </div>
          <div style={{ marginTop: 12, padding: '10px 14px', background: '#0f172a', borderRadius: 8, border: '1px solid #1e293b' }}>
            <p style={{ margin: 0, fontSize: 12, color: '#64748b', lineHeight: 1.6 }}>
              <strong style={{ color: '#94a3b8' }}>Formato do token inline:</strong>{' '}
              <code style={{ color: '#7dd3fc', background: '#0c1a30', padding: '2px 6px', borderRadius: 3 }}>
                [category:tk_a8f3:display_partial]
              </code>
              {' '}— substituído por <em>display_partial</em> na entrega ao cliente via WebSocket.
              Resolvido para o valor original por roles autorizados via MCP tools.
            </p>
          </div>
        </Section>
      </div>
    </div>
  )
}

// ── Sub-components ─────────────────────────────────────────────────────────────

function Section({ icon, title, desc, children }: {
  icon: string; title: string; desc: string; children: React.ReactNode
}) {
  return (
    <div style={{ background: '#0d1f38', border: '1px solid #1e293b', borderRadius: 10, padding: '18px 20px' }}>
      <div style={{ display: 'flex', alignItems: 'flex-start', gap: 12, marginBottom: 6 }}>
        <span style={{ fontSize: 20 }}>{icon}</span>
        <div>
          <div style={{ fontWeight: 700, fontSize: 14, color: '#e2e8f0' }}>{title}</div>
          <div style={{ fontSize: 12, color: '#64748b', marginTop: 3, lineHeight: 1.5 }}>{desc}</div>
        </div>
      </div>
      {children}
    </div>
  )
}

function ToggleCard({ label, sublabel, active, onToggle, saving, warning }: {
  label: string; sublabel: string; active: boolean
  onToggle: () => void; saving: boolean; warning?: string
}) {
  return (
    <div style={{
      flex: '1 1 220px', background: '#0f172a', borderRadius: 8,
      border: `1px solid ${active ? '#3b82f6' : '#1e293b'}`, padding: '14px 16px',
    }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <div>
          <div style={{ fontWeight: 600, fontSize: 13, color: '#e2e8f0' }}>{label}</div>
          <code style={{ fontSize: 10, color: '#475569' }}>{sublabel}</code>
        </div>
        <button
          onClick={onToggle}
          disabled={saving}
          style={{
            width: 48, height: 26, borderRadius: 13, border: 'none', cursor: 'pointer',
            background: active ? '#3b82f6' : '#1e293b', position: 'relative', transition: 'background 0.2s',
          }}
        >
          <span style={{
            position: 'absolute', top: 3, left: active ? 24 : 3,
            width: 20, height: 20, borderRadius: '50%',
            background: active ? '#fff' : '#64748b', transition: 'left 0.2s',
          }} />
        </button>
      </div>
      {active && warning && (
        <div style={{ marginTop: 8, fontSize: 11, color: '#fbbf24' }}>⚠ {warning}</div>
      )}
    </div>
  )
}

function RetentionEditor({ value, onSave, saving }: {
  value: number; onSave: (v: number) => void; saving: boolean
}) {
  const [editing, setEditing] = useState(false)
  const [draft,   setDraft]   = useState(String(value))

  function commit() {
    const n = parseInt(draft, 10)
    if (!isNaN(n) && n >= 1 && n <= 365) {
      onSave(n)
      setEditing(false)
    }
  }

  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
      {editing ? (
        <>
          <input
            type="number" min={1} max={365}
            value={draft}
            onChange={e => setDraft(e.target.value)}
            style={{ ...inputStyle, width: 80 }}
            onKeyDown={e => e.key === 'Enter' && commit()}
            autoFocus
          />
          <button onClick={commit} disabled={saving} style={saveBtnStyle}>
            {saving ? '…' : 'Salvar'}
          </button>
          <button onClick={() => setEditing(false)} style={cancelBtnStyle}>Cancelar</button>
        </>
      ) : (
        <>
          <div style={{
            fontSize: 28, fontWeight: 700, color: '#7dd3fc',
            lineHeight: 1, fontVariantNumeric: 'tabular-nums',
          }}>
            {value}
          </div>
          <div style={{ color: '#64748b', fontSize: 13 }}>dias</div>
          <button onClick={() => { setDraft(String(value)); setEditing(true) }} style={editBtnStyle}>
            ✏️ Editar
          </button>
        </>
      )}
    </div>
  )
}

// ── Styles ─────────────────────────────────────────────────────────────────────

const inputStyle: React.CSSProperties = {
  background: '#0f172a', border: '1px solid #334155', borderRadius: 6,
  color: '#e2e8f0', fontSize: 12, padding: '4px 10px', outline: 'none',
}

const iconBtn: React.CSSProperties = {
  background: 'none', border: 'none', color: '#64748b', cursor: 'pointer', fontSize: 14, padding: '0 4px',
}

const saveBtnStyle: React.CSSProperties = {
  padding: '5px 14px', fontSize: 12, fontWeight: 600, borderRadius: 6,
  background: '#3b82f6', color: '#fff', border: 'none', cursor: 'pointer',
}

const cancelBtnStyle: React.CSSProperties = {
  padding: '5px 14px', fontSize: 12, borderRadius: 6,
  background: 'none', color: '#64748b', border: '1px solid #334155', cursor: 'pointer',
}

const editBtnStyle: React.CSSProperties = {
  padding: '4px 12px', fontSize: 12, borderRadius: 6,
  background: 'none', color: '#64748b', border: '1px solid #334155', cursor: 'pointer',
}

function infoBox(bg: string, color: string): React.CSSProperties {
  return {
    margin: '8px 28px', padding: '10px 16px', background: bg,
    borderRadius: 8, fontSize: 13, color,
  }
}
