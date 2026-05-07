/**
 * WebChatConfigPage.tsx
 * Configuração do canal WebChat — lê/escreve namespace "webchat" no Config API.
 *
 * Três grupos de configuração:
 *   1. Autenticação  — auth_timeout_s
 *   2. Attachments   — attachment_expiry_days, upload_limits_mb (image/pdf/video)
 *   3. Sobre         — link para documentação e MIME allowlist
 *
 * Admin token é solicitado inline (mesmo padrão do NamespaceEditor).
 */
import React, { useState, useEffect, useCallback } from 'react'
import { useAuth } from '@/auth/useAuth'
import { useNamespace, putConfig } from '../config-plataforma/api/config-hooks'
import Spinner from '@/components/ui/Spinner'

// ── Types ──────────────────────────────────────────────────────────────────────

interface UploadLimits { image: number; pdf: number; video: number }

interface WebChatConfig {
  auth_timeout_s:       number
  attachment_expiry_days: number
  upload_limits_mb:     UploadLimits
}

const DEFAULTS: WebChatConfig = {
  auth_timeout_s:         30,
  attachment_expiry_days: 30,
  upload_limits_mb:       { image: 16, pdf: 100, video: 512 },
}

// ── Component ─────────────────────────────────────────────────────────────────

const WebChatConfigPage: React.FC = () => {
  const { tenantId } = useAuth()
  const { entries, loading, error, reload } = useNamespace(tenantId, 'webchat')

  const [adminToken, setAdminToken] = useState('')
  const [showToken,  setShowToken]  = useState(false)
  const [cfg,        setCfg]        = useState<WebChatConfig>(DEFAULTS)
  const [dirty,      setDirty]      = useState(false)
  const [saving,     setSaving]     = useState<string | null>(null) // key being saved
  const [saveErr,    setSaveErr]    = useState<string | null>(null)

  // Sync from API
  useEffect(() => {
    if (loading) return
    const parse = <T,>(key: string, fallback: T): T => {
      const e = entries[key]
      return e != null ? (e.value as T) : fallback
    }
    setCfg({
      auth_timeout_s:         parse('auth_timeout_s',         DEFAULTS.auth_timeout_s),
      attachment_expiry_days: parse('attachment_expiry_days', DEFAULTS.attachment_expiry_days),
      upload_limits_mb:       parse('upload_limits_mb',       DEFAULTS.upload_limits_mb),
    })
    setDirty(false)
  }, [loading, entries])

  const update = useCallback(<K extends keyof WebChatConfig>(key: K, val: WebChatConfig[K]) => {
    setCfg(prev => ({ ...prev, [key]: val }))
    setDirty(true)
    setSaveErr(null)
  }, [])

  const saveKey = useCallback(async (key: keyof WebChatConfig) => {
    if (!adminToken) { setSaveErr('Admin token obrigatório'); return }
    setSaving(key); setSaveErr(null)
    try {
      await putConfig('webchat', key, cfg[key], null, adminToken)
      setDirty(false)
      reload()
    } catch (e) {
      setSaveErr(String(e))
    } finally {
      setSaving(null)
    }
  }, [cfg, adminToken, reload])

  const saveAll = useCallback(async () => {
    if (!adminToken) { setSaveErr('Admin token obrigatório'); return }
    setSaving('all'); setSaveErr(null)
    try {
      await Promise.all([
        putConfig('webchat', 'auth_timeout_s',         cfg.auth_timeout_s,         null, adminToken),
        putConfig('webchat', 'attachment_expiry_days', cfg.attachment_expiry_days, null, adminToken),
        putConfig('webchat', 'upload_limits_mb',       cfg.upload_limits_mb,       null, adminToken),
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
    setDirty(true)
    setSaveErr(null)
  }, [])

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
          placeholder="Token para habilitar edição"
          className="flex-1 text-xs px-2 py-1.5 border border-gray-300 rounded focus:outline-none focus:border-primary"
        />
        <button
          onClick={() => setShowToken(v => !v)}
          className="text-gray-400 hover:text-gray-600 text-sm"
        >{showToken ? '🙈' : '👁'}</button>
        {adminToken && <span className="text-xs text-green-600 font-semibold shrink-0">✓ ativo</span>}
        <div className="flex items-center gap-2 ml-auto">
          {loading && <Spinner />}
          {error && <span className="text-xs text-red-600">⚠ {error}</span>}
          <button onClick={reload} className="text-xs text-secondary hover:text-primary">↻</button>
        </div>
      </div>

      {/* ── 1. Autenticação ── */}
      <section className="bg-white border border-gray-200 rounded-lg p-5">
        <h3 className="text-sm font-semibold text-gray-900 mb-1">Autenticação WebSocket</h3>
        <p className="text-xs text-gray-500 mb-4">
          Tempo máximo (segundos) que o servidor aguarda a mensagem <code className="font-mono bg-gray-100 px-1 rounded">conn.authenticate</code> após
          a conexão WebSocket ser aceita. A conexão é encerrada ao esgotar o prazo.
        </p>
        <div className="flex items-end gap-3">
          <div className="flex-1">
            <label className="text-xs font-medium text-dark block mb-1">
              Timeout de autenticação (s)
            </label>
            <input
              type="number"
              min={5} max={120} step={5}
              value={cfg.auth_timeout_s}
              onChange={e => update('auth_timeout_s', parseInt(e.target.value) || DEFAULTS.auth_timeout_s)}
              className="w-32 text-xs font-mono px-2 py-1.5 border border-gray-300 rounded focus:outline-none focus:border-primary"
            />
          </div>
          <SaveBtn
            onClick={() => saveKey('auth_timeout_s')}
            saving={saving === 'auth_timeout_s'}
            disabled={!adminToken}
          />
        </div>
      </section>

      {/* ── 2. Attachments ── */}
      <section className="bg-white border border-gray-200 rounded-lg p-5">
        <h3 className="text-sm font-semibold text-gray-900 mb-1">Política de Attachments</h3>
        <p className="text-xs text-gray-500 mb-4">
          Controla a expiração e os limites de tamanho por tipo de arquivo. A exclusão suave
          ocorre ao atingir <em>attachment_expiry_days</em>; a exclusão física acontece 24h depois.
        </p>

        {/* Expiry */}
        <div className="mb-4">
          <label className="text-xs font-medium text-dark block mb-1">Expiração (dias)</label>
          <div className="flex items-end gap-3">
            <input
              type="number"
              min={1} max={365} step={1}
              value={cfg.attachment_expiry_days}
              onChange={e => update('attachment_expiry_days', parseInt(e.target.value) || DEFAULTS.attachment_expiry_days)}
              className="w-24 text-xs font-mono px-2 py-1.5 border border-gray-300 rounded focus:outline-none focus:border-primary"
            />
          </div>
        </div>

        {/* Upload limits */}
        <label className="text-xs font-medium text-dark block mb-2">Limites de upload (MB)</label>
        <div className="grid grid-cols-3 gap-4 mb-4">
          {(
            [
              { key: 'image' as const, label: 'Imagem',  hint: 'JPEG / PNG / WebP / GIF' },
              { key: 'pdf'   as const, label: 'PDF',     hint: 'application/pdf' },
              { key: 'video' as const, label: 'Vídeo',   hint: 'MP4 / WebM' },
            ]
          ).map(({ key, label, hint }) => (
            <div key={key}>
              <label className="text-xs font-medium text-dark block mb-0.5">{label}</label>
              <p className="text-[10px] text-gray-400 mb-1">{hint}</p>
              <input
                type="number"
                min={1} max={2048} step={1}
                value={cfg.upload_limits_mb[key]}
                onChange={e => update('upload_limits_mb', {
                  ...cfg.upload_limits_mb,
                  [key]: parseInt(e.target.value) || DEFAULTS.upload_limits_mb[key],
                })}
                className="w-full text-xs font-mono px-2 py-1.5 border border-gray-300 rounded focus:outline-none focus:border-primary"
              />
            </div>
          ))}
        </div>

        <SaveBtn
          onClick={() => saveKey('attachment_expiry_days').then(() => saveKey('upload_limits_mb'))}
          saving={saving === 'attachment_expiry_days' || saving === 'upload_limits_mb'}
          disabled={!adminToken}
          label="Salvar política de attachments"
        />
      </section>

      {/* Global save + error */}
      {saveErr && <p className="text-xs text-red-600">{saveErr}</p>}

      <div className="flex gap-2">
        <button
          onClick={saveAll}
          disabled={saving !== null || !adminToken || !dirty}
          className="px-4 py-2 rounded text-sm font-semibold bg-primary text-white disabled:opacity-40 hover:bg-blue-800 transition-colors"
        >
          {saving === 'all' ? 'Salvando…' : '💾 Salvar todas as alterações'}
        </button>
        <button
          onClick={handleReset}
          className="px-4 py-2 rounded text-sm border border-gray-300 text-gray-600 hover:text-gray-900 transition-colors"
        >
          ↺ Restaurar padrões
        </button>
      </div>

      {/* Info */}
      <section className="bg-blue-50 border border-blue-200 rounded-lg p-4 text-xs text-blue-700 space-y-1">
        <p className="font-semibold">ℹ️ Sobre a configuração WebChat</p>
        <p>
          Estas configurações são lidas pelo <strong>Channel Gateway</strong> (porta 5000) em runtime via Config API.
          Alterações entram em vigor nas novas conexões WebSocket — sessões ativas não são afetadas.
        </p>
        <p>
          O <strong>JWT secret</strong> por tenant é configurado via Redis (<code className="font-mono">{'{'tenant_id{'}'}:config:webchat:jwt_secret'}</code>)
          e não é editável aqui por razões de segurança.
        </p>
      </section>
    </div>
  )
}

// ── SaveBtn helper ─────────────────────────────────────────────────────────────

function SaveBtn({ onClick, saving, disabled, label = 'Salvar' }: {
  onClick:  () => void
  saving:   boolean
  disabled: boolean
  label?:   string
}) {
  return (
    <button
      onClick={onClick}
      disabled={saving || disabled}
      className="px-3 py-1.5 rounded text-xs font-semibold bg-primary text-white disabled:opacity-40 hover:bg-blue-800 transition-colors"
    >
      {saving ? 'Salvando…' : label}
    </button>
  )
}

export default WebChatConfigPage
