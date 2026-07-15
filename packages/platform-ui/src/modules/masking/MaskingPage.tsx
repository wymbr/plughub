/**
 * MaskingPage — /config/masking
 *
 * Dedicated UI for message masking configuration.
 * Settings are stored in Config API (namespace "audit_policy") and read by
 * MaskingService in mcp-server-plughub via Redis cache fallback chain.
 *
 * Sections:
 *   1. Access Policy — who can see original_content (unmasked values)
 *   2. Audit Capture — whether to capture input/output in audit records
 *   3. Retention     — how long masked tokens are kept in Redis
 *   4. Rules overview — read-only list of DEFAULT_MASKING_RULES categories
 */
import React, { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Users, ClipboardList, Timer, Monitor, Archive, Lock, Check, X, AlertTriangle, Pencil } from 'lucide-react'
import { useAuth } from '@/auth/useAuth'
import { useNamespace, putConfig } from '../config-plataforma/api/config-hooks'
import type { DisplayScreen, DisplayVoice, MaskingDisplayRule } from '@/components/MaskedToken'
import { DEFAULT_DISPLAY_RULE } from '@/components/MaskedToken'

// ── Masking categories (mirrors DEFAULT_MASKING_RULES in schemas/audit.ts) ─────
// Labels are translated inline in the component
const DEFAULT_CATEGORIES = [
  { id: 'credit_card',  example: '****1234',      icon: '💳' },
  { id: 'cpf',          example: '***-00',        icon: '🪪' },
  { id: 'phone',        example: '(11) ****-4321', icon: '📞' },
  { id: 'email_addr',   example: 'j***@emp.com',  icon: '📧' },
  { id: 'iban',         example: 'BR***1234',     icon: '🏦' },
  { id: 'passport',     example: '***1234',       icon: '🛂' },
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

// ── ContextStore masking types (mirrors @plughub/schemas) ────────────────────

type ContextMaskingType =
  | 'plain' | 'hidden' | 'full' | 'last_2' | 'last_4'
  | 'first_1' | 'first_word' | 'email_domain' | 'financial'

interface ContextMaskingRule {
  pattern: string
  role:    'operator' | 'supervisor' | '*'
  type:    ContextMaskingType
  label?:  string
}

interface ContextMaskingConfig {
  rules:                       ContextMaskingRule[]
  default_unmatched_operator:  ContextMaskingType
  /** Roles treated as "supervisor" (bypass namespace gate, see PII plain). */
  supervisor_roles?:           string[]
}

const DEFAULT_SUPERVISOR_ROLES = ['supervisor', 'admin', 'evaluator', 'reviewer']

const MASKING_TYPE_INFO: Record<ContextMaskingType, { label: string; sample: string }> = {
  plain:        { label: 'Visível (sem máscara)',  sample: '11.222.333-45' },
  hidden:       { label: 'Oculto (remove campo)', sample: '(oculto)' },
  full:         { label: 'Totalmente mascarado',  sample: '***' },
  last_2:       { label: 'Últimos 2 dígitos',     sample: '***-45' },
  last_4:       { label: 'Últimos 4 dígitos',     sample: '****3345' },
  first_1:      { label: 'Primeira letra',        sample: 'J***' },
  first_word:   { label: 'Primeira palavra',      sample: 'João ***' },
  email_domain: { label: 'Domínio visível',       sample: 'j***@empresa.com' },
  financial:    { label: 'Valor financeiro',      sample: 'R$ ****,**' },
}

const EMPTY_CONTEXT_CONFIG: ContextMaskingConfig = {
  rules: [],
  default_unmatched_operator: 'plain',
  supervisor_roles: DEFAULT_SUPERVISOR_ROLES,
}

// ── Main component ────────────────────────────────────────────────────────────

export default function MaskingPage() {
  const { t } = useTranslation('masking')
  // G-PROBE platform-wide: escritas usam o Bearer do operador + ABAC `config.masking` —
  // sem caixa de admin-token. `adminToken` mantém o nome (=accessToken) p/ diff mínimo.
  const { tenantId, session } = useAuth()
  const adminToken = session?.accessToken ?? ''
  const [saving,       setSaving]       = useState<string | null>(null)
  const [toast,        setToast]        = useState<{ msg: string; ok: boolean } | null>(null)

  // audit_policy namespace is the canonical source for masking/audit configuration
  const { entries, loading, error, reload } = useNamespace(tenantId, 'audit_policy')

  // masking namespace stores per-category display rules
  const { entries: maskingEntries, reload: reloadMasking } = useNamespace(tenantId, 'masking')

  // Resolved values with defaults — entries[key].value holds the actual config value
  const val = (key: string): unknown => entries[key]?.value ?? entries[key]

  const authorizedRoles: string[] = Array.isArray(val('authorized_roles'))
    ? (val('authorized_roles') as string[])
    : ['evaluator', 'reviewer']

  const captureInput:  boolean = val('capture_input')  === true
  const captureOutput: boolean = val('capture_output') === true
  const retentionDays: number  = typeof val('token_retention_days') === 'number'
    ? (val('token_retention_days') as number)
    : 30

  function showToast(msg: string, ok: boolean) {
    setToast({ msg, ok })
    setTimeout(() => setToast(null), 3000)
  }

  async function saveKey(key: string, value: unknown) {
    if (!adminToken) { showToast(t('toast.tokenRequired'), false); return }
    setSaving(key)
    try {
      await putConfig('audit_policy', key, value, tenantId, '', adminToken)
      reload()
      showToast(t('toast.keySaved', { key }), true)
    } catch (e) {
      showToast(String(e), false)
    } finally {
      setSaving(null)
    }
  }

  async function saveMaskingRule(category: string, rule: MaskingDisplayRule) {
    if (!adminToken) { showToast(t('toast.tokenRequired'), false); return }
    const key = `rule.${category}`
    setSaving(key)
    try {
      await putConfig('masking', key, rule, tenantId, '', adminToken)
      reloadMasking()
      showToast(t('toast.keySaved', { key }), true)
    } catch (e) {
      showToast(String(e), false)
    } finally {
      setSaving(null)
    }
  }

  function getMaskingRule(category: string): MaskingDisplayRule {
    const key = `rule.${category}`
    const v = maskingEntries[key]?.value ?? maskingEntries[key]
    if (v && typeof v === 'object') return v as MaskingDisplayRule
    return DEFAULT_DISPLAY_RULE
  }

  // ── Context Store masking rules ─────────────────────────────────────────────

  const rawContextRules = maskingEntries['context_rules']?.value ?? maskingEntries['context_rules']
  const contextRulesConfig: ContextMaskingConfig = (
    rawContextRules && typeof rawContextRules === 'object'
  ) ? (rawContextRules as ContextMaskingConfig) : EMPTY_CONTEXT_CONFIG

  async function saveContextRules(config: ContextMaskingConfig) {
    if (!adminToken) { showToast(t('toast.tokenRequired'), false); return }
    setSaving('context_rules')
    try {
      await putConfig('masking', 'context_rules', config, tenantId, '', adminToken)
      reloadMasking()
      showToast('Regras de ContextStore salvas com sucesso', true)
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
          {toast.ok ? <Check size={13} style={{ display: 'inline', marginRight: 6 }} aria-hidden="true" /> : <X size={13} style={{ display: 'inline', marginRight: 6 }} aria-hidden="true" />}{toast.msg}
        </div>
      )}

      {/* Page header */}
      <div style={{ padding: '20px 28px 16px', borderBottom: '1px solid #1e293b' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 16 }}>
          <div>
            <h1 style={{ margin: 0, fontSize: 18, fontWeight: 700, color: '#e2e8f0' }}>
              <Lock size={18} style={{ display: 'inline', marginRight: 8, verticalAlign: 'middle' }} aria-hidden="true" />{t('header.title')}
            </h1>
            <p style={{ margin: '4px 0 0', fontSize: 13, color: '#64748b' }}>
              {t('header.description')}
            </p>
          </div>
        </div>
      </div>

      {/* Loading / error */}
      {loading && <div style={infoBox('#1e293b', '#94a3b8')}>{t('loading')}</div>}
      {error   && <div style={{ ...infoBox('#7f1d1d22', '#fca5a5'), display: 'flex', alignItems: 'center', gap: 6 }}><AlertTriangle size={14} aria-hidden="true" />{error}</div>}

      <div style={{ padding: '20px 28px', display: 'flex', flexDirection: 'column', gap: 24 }}>

        {/* ── Section 1: Access Policy ─────────────────────────────────────── */}
        <Section
          icon={Users}
          title={t('section.access.title')}
          desc={t('section.access.description')}
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
                  {active && <Check size={12} style={{ display: 'inline', marginRight: 4 }} aria-hidden="true" />}{role}
                </button>
              )
            })}
          </div>
          <p style={{ margin: '10px 0 0', fontSize: 12, color: '#475569' }}>
            {t('section.access.activeRoles', {
              roles: authorizedRoles.length === 0 ? t('section.access.noRoles') : authorizedRoles.join(', ')
            })}
          </p>
        </Section>

        {/* ── Section 2: Audit Capture ──────────────────────────────────────── */}
        <Section
          icon={ClipboardList}
          title={t('section.audit.title')}
          desc={t('section.audit.description')}
        >
          <div style={{ display: 'flex', gap: 16, marginTop: 12, flexWrap: 'wrap' }}>
            <ToggleCard
              label={t('section.audit.captureInput')}
              sublabel="capture_input"
              active={captureInput}
              onToggle={() => saveKey('capture_input', !captureInput)}
              saving={saving === 'capture_input'}
              warning={t('section.audit.warning')}
            />
            <ToggleCard
              label={t('section.audit.captureOutput')}
              sublabel="capture_output"
              active={captureOutput}
              onToggle={() => saveKey('capture_output', !captureOutput)}
              saving={saving === 'capture_output'}
            />
          </div>
        </Section>

        {/* ── Section 3: Token Retention ────────────────────────────────────── */}
        <Section
          icon={Timer}
          title={t('section.retention.title')}
          desc={t('section.retention.description')}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginTop: 12 }}>
            <RetentionEditor
              value={retentionDays}
              onSave={v => saveKey('token_retention_days', v)}
              saving={saving === 'token_retention_days'}
            />
          </div>
        </Section>

        {/* ── Section 4: Categories overview ───────────────────────────────── */}
        <Section
          icon={ClipboardList}
          title={t('section.categories.title')}
          desc={t('section.categories.description')}
        >
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(260px, 1fr))', gap: 10, marginTop: 12 }}>
            {DEFAULT_CATEGORIES.map(cat => (
              <div key={cat.id} style={{
                background: '#0f172a', border: '1px solid #1e293b', borderRadius: 8,
                padding: '12px 16px', display: 'flex', alignItems: 'flex-start', gap: 12,
              }}>
                <span style={{ fontSize: 22 }}>{cat.icon}</span>
                <div>
                  <div style={{ fontWeight: 600, fontSize: 13, color: '#e2e8f0' }}>{t(`categories.${cat.id}`)}</div>
                  <div style={{ fontSize: 11, color: '#64748b', marginTop: 2 }}>
                    {t('section.categories.display')}: <code style={{ color: '#94a3b8', background: '#1e293b', padding: '0 4px', borderRadius: 3 }}>{cat.example}</code>
                  </div>
                  <div style={{ marginTop: 6 }}>{badge(t('section.categories.active'), '#22c55e')}</div>
                </div>
              </div>
            ))}
          </div>
          <div style={{ marginTop: 12, padding: '10px 14px', background: '#0f172a', borderRadius: 8, border: '1px solid #1e293b' }}>
            <p style={{ margin: 0, fontSize: 12, color: '#64748b', lineHeight: 1.6 }}>
              <strong style={{ color: '#94a3b8' }}>{t('section.categories.tokenFormat')}:</strong>{' '}
              <code style={{ color: '#7dd3fc', background: '#0c1a30', padding: '2px 6px', borderRadius: 3 }}>
                [category:tk_a8f3:display_partial]
              </code>
              {' '}— {t('section.categories.tokenExplanation')}
            </p>
          </div>
        </Section>

        {/* ── Section 5: Display rules per category ────────────────────────── */}
        <Section
          icon={Monitor}
          title={t('section.displayRules.title', { defaultValue: 'Display Rules by Category' })}
          desc={t('section.displayRules.description', { defaultValue: 'Configure how masked tokens are shown per channel. Changes apply immediately to new sessions.' })}
        >
          <div style={{ display: 'flex', flexDirection: 'column', gap: 12, marginTop: 12 }}>
            {DEFAULT_CATEGORIES.map(cat => {
              const rule = getMaskingRule(cat.id)
              const key  = `rule.${cat.id}`
              const isSaving = saving === key
              function update(patch: Partial<MaskingDisplayRule>) {
                saveMaskingRule(cat.id, { ...rule, ...patch })
              }
              return (
                <div key={cat.id} style={{
                  background: '#0f172a', border: '1px solid #1e293b', borderRadius: 8,
                  padding: '14px 16px',
                }}>
                  {/* Category header */}
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 12 }}>
                    <span style={{ fontSize: 18 }}>{cat.icon}</span>
                    <span style={{ fontWeight: 600, fontSize: 13, color: '#e2e8f0' }}>{t(`categories.${cat.id}`)}</span>
                    <code style={{ fontSize: 10, color: '#475569', marginLeft: 4 }}>{cat.id}</code>
                    {isSaving && <span style={{ fontSize: 10, color: '#3b82f6', marginLeft: 'auto' }}>saving…</span>}
                  </div>

                  {/* Controls row */}
                  <div style={{ display: 'flex', flexWrap: 'wrap', gap: 16, alignItems: 'flex-start' }}>

                    {/* display_screen */}
                    <div style={{ display: 'flex', flexDirection: 'column', gap: 4, minWidth: 140 }}>
                      <label style={{ fontSize: 10, color: '#64748b', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.06em' }}>
                        {t('section.displayRules.screen', { defaultValue: 'Screen display' })}
                      </label>
                      <select
                        value={rule.display_screen}
                        disabled={isSaving}
                        onChange={e => update({ display_screen: e.target.value as DisplayScreen })}
                        style={{ ...selectStyle }}
                      >
                        <option value="display_partial">{t('displayScreen.partial', { defaultValue: 'Partial (***-00)' })}</option>
                        <option value="full_mask">{t('displayScreen.full', { defaultValue: 'Full mask (•••••)' })}</option>
                        <option value="hidden">{t('displayScreen.hidden', { defaultValue: 'Label only' })}</option>
                      </select>
                    </div>

                    {/* display_voice */}
                    <div style={{ display: 'flex', flexDirection: 'column', gap: 4, minWidth: 140 }}>
                      <label style={{ fontSize: 10, color: '#64748b', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.06em' }}>
                        {t('section.displayRules.voice', { defaultValue: 'Voice channel' })}
                      </label>
                      <select
                        value={rule.display_voice}
                        disabled={isSaving}
                        onChange={e => update({ display_voice: e.target.value as DisplayVoice })}
                        style={{ ...selectStyle }}
                      >
                        <option value="silence">{t('displayVoice.silence', { defaultValue: 'Silence' })}</option>
                        <option value="beep">{t('displayVoice.beep', { defaultValue: 'Beep tone' })}</option>
                        <option value="speak_placeholder">{t('displayVoice.placeholder', { defaultValue: 'Speak placeholder' })}</option>
                      </select>
                    </div>

                    {/* echo_to_customer */}
                    <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                      <label style={{ fontSize: 10, color: '#64748b', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.06em' }}>
                        {t('section.displayRules.echoCustomer', { defaultValue: 'Echo to customer' })}
                      </label>
                      <MiniToggle
                        active={rule.echo_to_customer}
                        onToggle={() => update({ echo_to_customer: !rule.echo_to_customer })}
                        disabled={isSaving}
                      />
                    </div>

                    {/* echo_to_operator */}
                    <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                      <label style={{ fontSize: 10, color: '#64748b', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.06em' }}>
                        {t('section.displayRules.echoOperator', { defaultValue: 'Echo to operator' })}
                      </label>
                      <MiniToggle
                        active={rule.echo_to_operator}
                        onToggle={() => update({ echo_to_operator: !rule.echo_to_operator })}
                        disabled={isSaving}
                      />
                    </div>
                  </div>
                </div>
              )
            })}
          </div>
        </Section>

        {/* ── Section 6: Context Store field-level masking rules ─────────── */}
        <Section
          icon={Archive}
          title="Regras de Context Store"
          desc="Controla como cada tag do ContextStore é exibida por role (operator / supervisor). Aplicado em /api/supervisor_state e no painel de contexto do Console. Regras com padrão mais específico têm prioridade."
        >
          <ContextRulesSection
            config={contextRulesConfig}
            saving={saving === 'context_rules'}
            onSave={saveContextRules}
          />
        </Section>
      </div>
    </div>
  )
}

// ── Sub-components ─────────────────────────────────────────────────────────────

function Section({ icon: SectionIcon, title, desc, children }: {
  icon: React.ElementType; title: string; desc: string; children: React.ReactNode
}) {
  return (
    <div style={{ background: '#0d1f38', border: '1px solid #1e293b', borderRadius: 10, padding: '18px 20px' }}>
      <div style={{ display: 'flex', alignItems: 'flex-start', gap: 12, marginBottom: 6 }}>
        <SectionIcon className="w-5 h-5 flex-shrink-0" style={{ color: '#94a3b8', marginTop: 2 } as React.CSSProperties} aria-hidden="true" />
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
        <div style={{ marginTop: 8, fontSize: 11, color: '#fbbf24', display: 'flex', alignItems: 'center', gap: 4 }}><AlertTriangle size={12} aria-hidden="true" />{warning}</div>
      )}
    </div>
  )
}

function RetentionEditor({ value, onSave, saving }: {
  value: number; onSave: (v: number) => void; saving: boolean
}) {
  const { t } = useTranslation('masking')
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
            {saving ? '…' : t('retention.save')}
          </button>
          <button onClick={() => setEditing(false)} style={cancelBtnStyle}>{t('retention.cancel')}</button>
        </>
      ) : (
        <>
          <div style={{
            fontSize: 28, fontWeight: 700, color: '#7dd3fc',
            lineHeight: 1, fontVariantNumeric: 'tabular-nums',
          }}>
            {value}
          </div>
          <div style={{ color: '#64748b', fontSize: 13 }}>{t('retention.days')}</div>
          <button onClick={() => { setDraft(String(value)); setEditing(true) }} style={editBtnStyle}>
            <Pencil size={13} style={{ display: 'inline', marginRight: 4 }} aria-hidden="true" />{t('retention.edit')}
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

function MiniToggle({ active, onToggle, disabled }: {
  active: boolean; onToggle: () => void; disabled?: boolean
}) {
  return (
    <button
      onClick={onToggle}
      disabled={disabled}
      style={{
        width: 40, height: 22, borderRadius: 11, border: 'none', cursor: disabled ? 'not-allowed' : 'pointer',
        background: active ? '#3b82f6' : '#1e293b', position: 'relative', transition: 'background 0.2s',
        opacity: disabled ? 0.6 : 1,
      }}
    >
      <span style={{
        position: 'absolute', top: 2, left: active ? 20 : 2,
        width: 18, height: 18, borderRadius: '50%',
        background: active ? '#fff' : '#64748b', transition: 'left 0.2s',
      }} />
    </button>
  )
}

const selectStyle: React.CSSProperties = {
  background: '#0a1628', border: '1px solid #334155', borderRadius: 6,
  color: '#e2e8f0', fontSize: 12, padding: '5px 8px', outline: 'none', cursor: 'pointer',
}

function infoBox(bg: string, color: string): React.CSSProperties {
  return {
    margin: '8px 28px', padding: '10px 16px', background: bg,
    borderRadius: 8, fontSize: 13, color,
  }
}

// ── ContextRulesSection ────────────────────────────────────────────────────────

const EMPTY_RULE: ContextMaskingRule = { pattern: '', role: 'operator', type: 'plain', label: '' }
const ALL_TYPES = Object.keys(MASKING_TYPE_INFO) as ContextMaskingType[]
const ROLE_OPTIONS: Array<ContextMaskingRule['role']> = ['operator', 'supervisor', '*']

function ContextRulesSection({
  config,
  saving,
  onSave,
}: {
  config:  ContextMaskingConfig
  saving:  boolean
  onSave:  (config: ContextMaskingConfig) => void
}) {
  const [rules,       setRules]       = useState<ContextMaskingRule[]>(config.rules)
  const [defaultType, setDefaultType] = useState<ContextMaskingType>(config.default_unmatched_operator)
  const [supRoles,    setSupRoles]    = useState<string>((config.supervisor_roles ?? DEFAULT_SUPERVISOR_ROLES).join(', '))
  const [editIndex,   setEditIndex]   = useState<number | null>(null)
  const [editDraft,   setEditDraft]   = useState<ContextMaskingRule>(EMPTY_RULE)
  const [newDraft,    setNewDraft]    = useState<ContextMaskingRule>(EMPTY_RULE)
  const [showAddRow,  setShowAddRow]  = useState(false)

  // Sync when parent config changes (e.g. after reload)
  React.useEffect(() => {
    setRules(config.rules)
    setDefaultType(config.default_unmatched_operator)
    setSupRoles((config.supervisor_roles ?? DEFAULT_SUPERVISOR_ROLES).join(', '))
  }, [config])

  const supRolesArr = supRoles.split(',').map(s => s.trim()).filter(Boolean)

  function startEdit(index: number) {
    setEditIndex(index)
    setEditDraft({ ...rules[index]! })
  }

  function cancelEdit() {
    setEditIndex(null)
  }

  function commitEdit() {
    if (!editDraft.pattern.trim()) return
    const next = rules.map((r, i) => i === editIndex ? { ...editDraft, pattern: editDraft.pattern.trim() } : r)
    setRules(next)
    setEditIndex(null)
  }

  function deleteRule(index: number) {
    setRules(rules.filter((_, i) => i !== index))
    if (editIndex === index) setEditIndex(null)
  }

  function commitAdd() {
    if (!newDraft.pattern.trim()) return
    setRules([...rules, { ...newDraft, pattern: newDraft.pattern.trim() }])
    setNewDraft(EMPTY_RULE)
    setShowAddRow(false)
  }

  function handleSave() {
    onSave({ rules, default_unmatched_operator: defaultType, supervisor_roles: supRolesArr })
  }

  const hasChanges =
    defaultType !== config.default_unmatched_operator ||
    JSON.stringify(rules) !== JSON.stringify(config.rules) ||
    JSON.stringify(supRolesArr) !== JSON.stringify(config.supervisor_roles ?? DEFAULT_SUPERVISOR_ROLES)

  return (
    <div style={{ marginTop: 16 }}>
      {/* Rules table */}
      <div style={{ overflowX: 'auto', marginBottom: 14 }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
          <thead>
            <tr style={{ borderBottom: '1px solid #1e293b' }}>
              {['Padrão (tag)', 'Role', 'Tipo de máscara', 'Prévia', 'Label', ''].map(h => (
                <th key={h} style={{ padding: '6px 10px', textAlign: 'left', color: '#64748b', fontWeight: 600, whiteSpace: 'nowrap', fontSize: 11 }}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rules.length === 0 && (
              <tr>
                <td colSpan={6} style={{ padding: '16px 10px', color: '#475569', textAlign: 'center', fontStyle: 'italic' }}>
                  Nenhuma regra configurada — usando defaults do sistema
                </td>
              </tr>
            )}
            {rules.map((rule, i) => (
              editIndex === i ? (
                <tr key={i} style={{ background: '#0f1f3a', borderBottom: '1px solid #1e3a5f' }}>
                  <td style={{ padding: '6px 8px' }}>
                    <input
                      value={editDraft.pattern}
                      onChange={e => setEditDraft(d => ({ ...d, pattern: e.target.value }))}
                      placeholder="caller.cpf ou caller.*"
                      style={{ ...inputStyle, width: 160 }}
                    />
                  </td>
                  <td style={{ padding: '6px 8px' }}>
                    <select value={editDraft.role} onChange={e => setEditDraft(d => ({ ...d, role: e.target.value as ContextMaskingRule['role'] }))} style={selectStyle}>
                      {ROLE_OPTIONS.map(r => <option key={r} value={r}>{r}</option>)}
                    </select>
                  </td>
                  <td style={{ padding: '6px 8px' }}>
                    <select value={editDraft.type} onChange={e => setEditDraft(d => ({ ...d, type: e.target.value as ContextMaskingType }))} style={selectStyle}>
                      {ALL_TYPES.map(t => <option key={t} value={t}>{MASKING_TYPE_INFO[t].label}</option>)}
                    </select>
                  </td>
                  <td style={{ padding: '6px 8px' }}>
                    <code style={{ color: '#7dd3fc', fontSize: 11 }}>{MASKING_TYPE_INFO[editDraft.type].sample}</code>
                  </td>
                  <td style={{ padding: '6px 8px' }}>
                    <input
                      value={editDraft.label ?? ''}
                      onChange={e => setEditDraft(d => ({ ...d, label: e.target.value }))}
                      placeholder="Descrição opcional"
                      style={{ ...inputStyle, width: 140 }}
                    />
                  </td>
                  <td style={{ padding: '6px 8px', whiteSpace: 'nowrap' }}>
                    <button onClick={commitEdit} style={{ ...saveBtnStyle, fontSize: 11, padding: '3px 10px', marginRight: 6 }} aria-label="Salvar"><Check size={12} aria-hidden="true" /></button>
                    <button onClick={cancelEdit} style={{ ...cancelBtnStyle, fontSize: 11, padding: '3px 10px' }} aria-label="Cancelar"><X size={12} aria-hidden="true" /></button>
                  </td>
                </tr>
              ) : (
                <tr key={i} style={{ borderBottom: '1px solid #1a2640', transition: 'background 0.1s' }}
                  onMouseEnter={e => (e.currentTarget.style.background = '#0f1a2e')}
                  onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}>
                  <td style={{ padding: '7px 10px' }}>
                    <code style={{ color: '#7dd3fc', fontSize: 12, background: '#0c1a30', padding: '1px 5px', borderRadius: 3 }}>{rule.pattern}</code>
                  </td>
                  <td style={{ padding: '7px 10px' }}>
                    <span style={{
                      fontSize: 11, fontWeight: 600,
                      color: rule.role === 'supervisor' ? '#a78bfa' : rule.role === '*' ? '#94a3b8' : '#60a5fa',
                    }}>{rule.role}</span>
                  </td>
                  <td style={{ padding: '7px 10px', color: '#cbd5e1' }}>{MASKING_TYPE_INFO[rule.type].label}</td>
                  <td style={{ padding: '7px 10px' }}>
                    <code style={{ color: '#f59e0b', fontSize: 11 }}>{MASKING_TYPE_INFO[rule.type].sample}</code>
                  </td>
                  <td style={{ padding: '7px 10px', color: '#64748b' }}>{rule.label ?? '—'}</td>
                  <td style={{ padding: '7px 10px', whiteSpace: 'nowrap' }}>
                    <button onClick={() => startEdit(i)} style={{ ...editBtnStyle, fontSize: 11, padding: '2px 8px', marginRight: 6 }} aria-label="Editar"><Pencil size={11} aria-hidden="true" /></button>
                    <button onClick={() => deleteRule(i)} style={{ ...cancelBtnStyle, fontSize: 11, padding: '2px 8px', color: '#ef4444', borderColor: '#ef444444' }} aria-label="Excluir"><X size={11} aria-hidden="true" /></button>
                  </td>
                </tr>
              )
            ))}

            {/* Add-new row */}
            {showAddRow && (
              <tr style={{ background: '#0b1e38', borderBottom: '1px solid #1e3a5f' }}>
                <td style={{ padding: '6px 8px' }}>
                  <input
                    value={newDraft.pattern}
                    onChange={e => setNewDraft(d => ({ ...d, pattern: e.target.value }))}
                    placeholder="caller.cpf ou caller.*"
                    style={{ ...inputStyle, width: 160 }}
                    autoFocus
                  />
                </td>
                <td style={{ padding: '6px 8px' }}>
                  <select value={newDraft.role} onChange={e => setNewDraft(d => ({ ...d, role: e.target.value as ContextMaskingRule['role'] }))} style={selectStyle}>
                    {ROLE_OPTIONS.map(r => <option key={r} value={r}>{r}</option>)}
                  </select>
                </td>
                <td style={{ padding: '6px 8px' }}>
                  <select value={newDraft.type} onChange={e => setNewDraft(d => ({ ...d, type: e.target.value as ContextMaskingType }))} style={selectStyle}>
                    {ALL_TYPES.map(t => <option key={t} value={t}>{MASKING_TYPE_INFO[t].label}</option>)}
                  </select>
                </td>
                <td style={{ padding: '6px 8px' }}>
                  <code style={{ color: '#7dd3fc', fontSize: 11 }}>{MASKING_TYPE_INFO[newDraft.type].sample}</code>
                </td>
                <td style={{ padding: '6px 8px' }}>
                  <input
                    value={newDraft.label ?? ''}
                    onChange={e => setNewDraft(d => ({ ...d, label: e.target.value }))}
                    placeholder="Descrição opcional"
                    style={{ ...inputStyle, width: 140 }}
                  />
                </td>
                <td style={{ padding: '6px 8px', whiteSpace: 'nowrap' }}>
                  <button onClick={commitAdd} style={{ ...saveBtnStyle, fontSize: 11, padding: '3px 10px', marginRight: 6 }}>+ Adicionar</button>
                  <button onClick={() => { setShowAddRow(false); setNewDraft(EMPTY_RULE) }} style={{ ...cancelBtnStyle, fontSize: 11, padding: '3px 10px' }} aria-label="Cancelar"><X size={12} aria-hidden="true" /></button>
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {/* Add row button */}
      {!showAddRow && (
        <button
          onClick={() => setShowAddRow(true)}
          style={{ ...editBtnStyle, fontSize: 12, marginBottom: 16 }}
        >
          + Nova regra
        </button>
      )}

      {/* Default unmatched operator */}
      <div style={{
        display: 'flex', alignItems: 'center', gap: 12, marginBottom: 16,
        padding: '12px 14px', background: '#0f172a', borderRadius: 8, border: '1px solid #1e293b',
      }}>
        <div style={{ flex: 1 }}>
          <div style={{ fontSize: 12, fontWeight: 600, color: '#e2e8f0' }}>Padrão para operator sem regra</div>
          <div style={{ fontSize: 11, color: '#64748b', marginTop: 2 }}>
            Aplicado quando nenhuma regra casa com a tag para o role <code style={{ color: '#94a3b8' }}>operator</code>.
            Supervisors sem regra sempre veem <code style={{ color: '#94a3b8' }}>plain</code>.
          </div>
        </div>
        <select
          value={defaultType}
          onChange={e => setDefaultType(e.target.value as ContextMaskingType)}
          style={{ ...selectStyle, minWidth: 180 }}
        >
          {ALL_TYPES.map(t => (
            <option key={t} value={t}>{MASKING_TYPE_INFO[t].label} — {MASKING_TYPE_INFO[t].sample}</option>
          ))}
        </select>
      </div>

      {/* Supervisor roles — who bypasses the namespace gate and sees PII plain */}
      <div style={{
        display: 'flex', alignItems: 'center', gap: 12, marginBottom: 16,
        padding: '12px 14px', background: '#0f172a', borderRadius: 8, border: '1px solid #1e293b',
      }}>
        <div style={{ flex: 1 }}>
          <div style={{ fontSize: 12, fontWeight: 600, color: '#e2e8f0' }}>Roles tratados como supervisor</div>
          <div style={{ fontSize: 11, color: '#64748b', marginTop: 2 }}>
            Estes roles ignoram a namespace gate e veem os campos <code style={{ color: '#94a3b8' }}>plain</code>.
            Qualquer role fora da lista é tratado como <code style={{ color: '#94a3b8' }}>operator</code>. Separe por vírgula.
          </div>
        </div>
        <input
          type="text"
          value={supRoles}
          onChange={e => setSupRoles(e.target.value)}
          placeholder="supervisor, admin, evaluator, reviewer"
          style={{ ...selectStyle, minWidth: 260 }}
        />
      </div>

      {/* Save */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
        <button
          onClick={handleSave}
          disabled={saving || !hasChanges}
          style={{
            ...saveBtnStyle,
            opacity: saving || !hasChanges ? 0.5 : 1,
            cursor: saving || !hasChanges ? 'not-allowed' : 'pointer',
          }}
        >
          {saving ? 'Salvando…' : 'Salvar Regras'}
        </button>
        {hasChanges && (
          <span style={{ fontSize: 11, color: '#fbbf24', display: 'inline-flex', alignItems: 'center', gap: 4 }}><AlertTriangle size={12} aria-hidden="true" />Alterações não salvas</span>
        )}
        {!hasChanges && rules.length > 0 && (
          <span style={{ fontSize: 11, color: '#22c55e', display: 'inline-flex', alignItems: 'center', gap: 4 }}><Check size={12} aria-hidden="true" />Sincronizado</span>
        )}
        <span style={{ fontSize: 11, color: '#475569', marginLeft: 'auto' }}>
          {rules.length} {rules.length === 1 ? 'regra' : 'regras'} configuradas
        </span>
      </div>

      {/* Hint about specificity */}
      <div style={{ marginTop: 12, padding: '8px 12px', background: '#0c1520', borderRadius: 6, border: '1px solid #1e293b' }}>
        <p style={{ margin: 0, fontSize: 11, color: '#475569', lineHeight: 1.6 }}>
          <strong style={{ color: '#64748b' }}>Prioridade de regras:</strong>{' '}
          padrão exato (<code style={{ color: '#7dd3fc' }}>caller.cpf</code>) &gt; glob de namespace (<code style={{ color: '#7dd3fc' }}>caller.*</code>) &gt; curinga (<code style={{ color: '#7dd3fc' }}>*</code>).
          Role exato supera <code style={{ color: '#7dd3fc' }}>*</code>. Role <code style={{ color: '#a78bfa' }}>supervisor</code> cobre supervisor, admin, evaluator e reviewer.
        </p>
      </div>
    </div>
  )
}
