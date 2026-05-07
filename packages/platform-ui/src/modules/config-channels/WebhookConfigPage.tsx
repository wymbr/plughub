/**
 * WebhookConfigPage.tsx
 * Webhook channel configuration — reads/writes namespace "webhook" in Config API.
 *
 * NOTE: this page configures the **channel webhook** adapter (inbound contacts
 * routed to a pool). It is distinct from workflow webhooks (Arc 4) which trigger
 * skill flows directly and live in Workflow → Webhooks / Triggers.
 *
 * Three configuration groups:
 *   1. Security     — HMAC signature verification (secret + algorithm)
 *   2. Access       — IP/CIDR allowlist
 *   3. Timeouts     — request timeout, response window
 */
import React, { useState, useEffect, useCallback } from 'react'
import { useAuth } from '@/auth/useAuth'
import { useNamespace, putConfig } from '../config-plataforma/api/config-hooks'
import Spinner from '@/components/ui/Spinner'

// ── Types ──────────────────────────────────────────────────────────────────────

interface WebhookConfig {
  hmac_enabled:         boolean
  hmac_algorithm:       'sha256' | 'sha512'
  hmac_header:          string
  ip_allowlist:         string[]   // CIDR blocks or IPs — empty = all allowed
  request_timeout_ms:   number
  response_window_ms:   number
}

const DEFAULTS: WebhookConfig = {
  hmac_enabled:         false,
  hmac_algorithm:       'sha256',
  hmac_header:          'X-Plughub-Signature',
  ip_allowlist:         [],
  request_timeout_ms:   5000,
  response_window_ms:   30000,
}

// ── Component ─────────────────────────────────────────────────────────────────

const WebhookConfigPage: React.FC = () => {
  const { tenantId } = useAuth()
  const { entries, loading, error, reload } = useNamespace(tenantId, 'webhook')

  const [adminToken,   setAdminToken]   = useState('')
  const [showToken,    setShowToken]    = useState(false)
  const [cfg,          setCfg]          = useState<WebhookConfig>(DEFAULTS)
  const [dirty,        setDirty]        = useState(false)
  const [saving,       setSaving]       = useState<string | null>(null)
  const [saveErr,      setSaveErr]      = useState<string | null>(null)
  const [allowlistRaw, setAllowlistRaw] = useState('')  // newline-separated input

  // Sync from API
  useEffect(() => {
    if (loading) return
    const parse = <T,>(key: string, fallback: T): T => {
      const e = entries[key]
      return e != null ? (e.value as T) : fallback
    }
    const loaded: WebhookConfig = {
      hmac_enabled:       parse('hmac_enabled',       DEFAULTS.hmac_enabled),
      hmac_algorithm:     parse('hmac_algorithm',     DEFAULTS.hmac_algorithm),
      hmac_header:        parse('hmac_header',        DEFAULTS.hmac_header),
      ip_allowlist:       parse('ip_allowlist',       DEFAULTS.ip_allowlist),
      request_timeout_ms: parse('request_timeout_ms', DEFAULTS.request_timeout_ms),
      response_window_ms: parse('response_window_ms', DEFAULTS.response_window_ms),
    }
    setCfg(loaded)
    setAllowlistRaw(loaded.ip_allowlist.join('\n'))
    setDirty(false)
  }, [loading, entries])

  const update = useCallback(<K extends keyof WebhookConfig>(key: K, val: WebhookConfig[K]) => {
    setCfg(prev => ({ ...prev, [key]: val }))
    setDirty(true)
    setSaveErr(null)
  }, [])

  // Parse allowlist textarea → array and sync to cfg
  function handleAllowlistChange(raw: string) {
    setAllowlistRaw(raw)
    const parsed = raw.split('\n').map(s => s.trim()).filter(Boolean)
    update('ip_allowlist', parsed)
  }

  const saveAll = useCallback(async () => {
    if (!adminToken) { setSaveErr('Admin token required'); return }
    setSaving('all'); setSaveErr(null)
    try {
      await Promise.all([
        putConfig('webhook', 'hmac_enabled',       cfg.hmac_enabled,       null, adminToken),
        putConfig('webhook', 'hmac_algorithm',     cfg.hmac_algorithm,     null, adminToken),
        putConfig('webhook', 'hmac_header',        cfg.hmac_header,        null, adminToken),
        putConfig('webhook', 'ip_allowlist',       cfg.ip_allowlist,       null, adminToken),
        putConfig('webhook', 'request_timeout_ms', cfg.request_timeout_ms, null, adminToken),
        putConfig('webhook', 'response_window_ms', cfg.response_window_ms, null, adminToken),
      ])
      setDirty(false)
      reload()
    } catch (e) {
      setSaveErr(String(e))
    } finally {
      setSaving(null)
    }
  }, [cfg, adminToken, reload])

  const handleReset = useCallback(() => {
    setCfg(DEFAULTS)
    setAllowlistRaw(DEFAULTS.ip_allowlist.join('\n'))
    setDirty(true)
    setSaveErr(null)
  }, [])

  const inp = 'text-xs border border-gray-300 rounded px-2 py-1.5 focus:outline-none focus:border-primary font-mono'

  // ── Render ───────────────────────────────────────────────────────────────────

  return (
    <div className="max-w-2xl space-y-6">

      {/* Admin token */}
      <div className="flex items-center gap-3 p-3 bg-gray-50 border border-gray-200 rounded-lg">
        <label className="text-xs font-medium text-gray-600 shrink-0">Admin Token</label>
        <input
          type={showToken ? 'text' : 'password'}
          value={adminToken}
          onChange={e => setAdminToken(e.target.value)}
          placeholder="Token to enable editing"
          className="flex-1 text-xs px-2 py-1.5 border border-gray-300 rounded focus:outline-none focus:border-primary"
        />
        <button
          onClick={() => setShowToken(v => !v)}
          className="text-gray-400 hover:text-gray-600 text-sm"
        >{showToken ? '🙈' : '👁'}</button>
        {adminToken && <span className="text-xs text-green-600 font-semibold shrink-0">✓ active</span>}
        <div className="flex items-center gap-2 ml-auto">
          {loading && <Spinner />}
          {error && <span className="text-xs text-red-600">⚠ {error}</span>}
          <button onClick={reload} className="text-xs text-secondary hover:text-primary">↻</button>
        </div>
      </div>

      {/* ── 1. HMAC Signature Verification ── */}
      <section className="bg-white border border-gray-200 rounded-lg p-5 space-y-4">
        <div>
          <h3 className="text-sm font-semibold text-gray-900 mb-1">HMAC Signature Verification</h3>
          <p className="text-xs text-gray-500">
            When enabled, the channel gateway verifies each incoming request against a shared secret.
            The sender must include an HMAC signature in the configured header.
          </p>
        </div>

        {/* Enable toggle */}
        <label className="flex items-center gap-2 cursor-pointer">
          <input
            type="checkbox"
            checked={cfg.hmac_enabled}
            onChange={e => update('hmac_enabled', e.target.checked)}
            className="w-4 h-4"
          />
          <span className="text-sm font-medium text-gray-700">Enable HMAC verification</span>
        </label>

        {cfg.hmac_enabled && (
          <div className="grid grid-cols-2 gap-4 pt-1">
            {/* Algorithm */}
            <div>
              <label className="text-xs font-medium text-gray-700 block mb-1">Algorithm</label>
              <select
                value={cfg.hmac_algorithm}
                onChange={e => update('hmac_algorithm', e.target.value as 'sha256' | 'sha512')}
                className={inp + ' w-full'}
              >
                <option value="sha256">HMAC-SHA256</option>
                <option value="sha512">HMAC-SHA512</option>
              </select>
            </div>

            {/* Header name */}
            <div>
              <label className="text-xs font-medium text-gray-700 block mb-1">Signature header</label>
              <input
                type="text"
                value={cfg.hmac_header}
                onChange={e => update('hmac_header', e.target.value)}
                placeholder="X-Plughub-Signature"
                className={inp + ' w-full'}
              />
            </div>
          </div>
        )}

        {cfg.hmac_enabled && (
          <div className="p-3 bg-amber-50 border border-amber-200 rounded text-xs text-amber-700">
            <strong>Shared secret</strong> — configured per endpoint via Redis key
            <code className="font-mono bg-amber-100 px-1 mx-1 rounded">
              {'{'}{'{tenant_id}'}:webhook:secret:{'{'}{'{identifier}'}{'}'}
            </code>
            and is not editable here for security reasons.
          </div>
        )}
      </section>

      {/* ── 2. IP Allowlist ── */}
      <section className="bg-white border border-gray-200 rounded-lg p-5 space-y-3">
        <div>
          <h3 className="text-sm font-semibold text-gray-900 mb-1">IP / CIDR Allowlist</h3>
          <p className="text-xs text-gray-500">
            One IP address or CIDR block per line. Leave empty to allow all sources.
            Applied globally to all webhook endpoints for this tenant.
          </p>
        </div>
        <textarea
          value={allowlistRaw}
          onChange={e => handleAllowlistChange(e.target.value)}
          rows={5}
          placeholder={'192.168.1.0/24\n10.0.0.1\n2001:db8::/32'}
          className="w-full text-xs font-mono px-2 py-1.5 border border-gray-300 rounded focus:outline-none focus:border-primary resize-y"
        />
        {cfg.ip_allowlist.length === 0 && (
          <p className="text-xs text-amber-600">⚠ Empty allowlist — all source IPs are accepted.</p>
        )}
      </section>

      {/* ── 3. Timeouts ── */}
      <section className="bg-white border border-gray-200 rounded-lg p-5 space-y-4">
        <div>
          <h3 className="text-sm font-semibold text-gray-900 mb-1">Timeouts</h3>
          <p className="text-xs text-gray-500">
            <strong>Request timeout</strong>: how long the gateway waits to read the full incoming payload.
            <br />
            <strong>Response window</strong>: how long the sender can wait for an async result (used when webhooks are synchronous).
          </p>
        </div>
        <div className="grid grid-cols-2 gap-4">
          <div>
            <label className="text-xs font-medium text-gray-700 block mb-1">Request timeout (ms)</label>
            <input
              type="number" min={500} max={30000} step={500}
              value={cfg.request_timeout_ms}
              onChange={e => update('request_timeout_ms', parseInt(e.target.value) || DEFAULTS.request_timeout_ms)}
              className={inp + ' w-full'}
            />
          </div>
          <div>
            <label className="text-xs font-medium text-gray-700 block mb-1">Response window (ms)</label>
            <input
              type="number" min={1000} max={120000} step={1000}
              value={cfg.response_window_ms}
              onChange={e => update('response_window_ms', parseInt(e.target.value) || DEFAULTS.response_window_ms)}
              className={inp + ' w-full'}
            />
          </div>
        </div>
      </section>

      {/* Error */}
      {saveErr && <p className="text-xs text-red-600">{saveErr}</p>}

      {/* Actions */}
      <div className="flex gap-2">
        <button
          onClick={saveAll}
          disabled={saving !== null || !adminToken || !dirty}
          className="px-4 py-2 rounded text-sm font-semibold bg-primary text-white disabled:opacity-40 hover:bg-blue-800 transition-colors"
        >
          {saving === 'all' ? 'Saving…' : '💾 Save all changes'}
        </button>
        <button
          onClick={handleReset}
          className="px-4 py-2 rounded text-sm border border-gray-300 text-gray-600 hover:text-gray-900 transition-colors"
        >
          ↺ Restore defaults
        </button>
      </div>

      {/* Info box */}
      <section className="bg-blue-50 border border-blue-200 rounded-lg p-4 text-xs text-blue-700 space-y-2">
        <p className="font-semibold">ℹ️ About channel webhooks vs. workflow webhooks</p>
        <p>
          <strong>Channel webhooks</strong> (this page) receive inbound contacts from external systems
          and route them to a pool via the routing engine. URL pattern:
          <code className="font-mono bg-blue-100 px-1 mx-1 rounded">{'{host}/channel/webhook/{identifier}'}</code>
        </p>
        <p>
          <strong>Workflow webhooks</strong> trigger a specific skill flow directly, bypassing routing.
          Managed in <em>Workflow → Webhooks / Triggers</em>. URL pattern:
          <code className="font-mono bg-blue-100 px-1 mx-1 rounded">{'{host}/v1/workflow/webhook/{id}'}</code>
        </p>
      </section>
    </div>
  )
}

export default WebhookConfigPage
