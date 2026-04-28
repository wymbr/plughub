/**
 * WebhooksTab.tsx
 * Webhook Trigger management — migrated from operator-console/WebhookPanel.tsx.
 * Uses Tailwind + platform-ui design tokens.
 */
import React, { useState } from 'react'
import { useAuth } from '@/auth/useAuth'
import {
  useWebhooks, useWebhookDeliveries,
  createWebhookApi, patchWebhookApi, rotateWebhookTokenApi, deleteWebhookApi,
} from './api/hooks'
import type { Webhook, WebhookDelivery } from './api/hooks'
import Button from '@/components/ui/Button'
import Spinner from '@/components/ui/Spinner'
import Input from '@/components/ui/Input'

// ─── CopyBox ──────────────────────────────────────────────────────────────────

function CopyBox({ value, label }: { value: string; label: string }) {
  const [copied, setCopied] = useState(false)
  function copy() {
    navigator.clipboard.writeText(value).then(() => {
      setCopied(true); setTimeout(() => setCopied(false), 2000)
    })
  }
  return (
    <div className="border border-primary rounded-lg p-3 bg-primary/5 mt-3">
      <div className="text-xs font-bold text-primary mb-2">
        ⚠ {label} — copie agora, não será exibido novamente
      </div>
      <div className="flex items-start gap-3">
        <code className="flex-1 text-xs text-secondary font-mono break-all leading-relaxed">
          {value}
        </code>
        <button
          onClick={copy}
          className={`shrink-0 text-xs font-semibold px-3 py-1 rounded border ${
            copied ? 'border-green text-green' : 'border-primary text-primary'
          } bg-transparent cursor-pointer`}
        >
          {copied ? '✓ Copiado' : 'Copiar'}
        </button>
      </div>
    </div>
  )
}

// ─── StatusBadge ──────────────────────────────────────────────────────────────

function HttpBadge({ code }: { code: number }) {
  const ok   = code >= 200 && code < 300
  const warn = code >= 400 && code < 500
  const cls  = ok ? 'bg-green/10 text-green border-green/30'
             : warn ? 'bg-warning/10 text-warning border-warning/30'
             : 'bg-red/10 text-red border-red/30'
  return <span className={`text-xs font-bold px-1.5 py-0.5 rounded border ${cls}`}>{code}</span>
}

function ActiveBadge({ active }: { active: boolean }) {
  return (
    <span className={`text-xs font-bold px-2 py-0.5 rounded-full border ${
      active ? 'bg-green/10 text-green border-green/30' : 'bg-red/10 text-red border-red/30'
    }`}>
      {active ? 'Ativo' : 'Inativo'}
    </span>
  )
}

// ─── CreateForm ───────────────────────────────────────────────────────────────

function CreateForm({ tenantId, adminToken, onCreated, onCancel }: {
  tenantId:   string
  adminToken: string
  onCreated:  (webhook: Webhook, token: string) => void
  onCancel:   () => void
}) {
  const [flowId,  setFlowId]  = useState('')
  const [desc,    setDesc]    = useState('')
  const [ctxRaw,  setCtxRaw]  = useState('{}')
  const [ctxErr,  setCtxErr]  = useState(false)
  const [saving,  setSaving]  = useState(false)
  const [error,   setError]   = useState('')

  function validateCtx(v: string) {
    setCtxRaw(v)
    try { JSON.parse(v); setCtxErr(false) } catch { setCtxErr(true) }
  }

  async function submit() {
    if (!flowId.trim()) return
    let ctx: Record<string, unknown> = {}
    try { ctx = JSON.parse(ctxRaw) } catch { setCtxErr(true); return }
    setSaving(true); setError('')
    try {
      const { webhook, token } = await createWebhookApi(tenantId, flowId.trim(), desc.trim(), ctx, adminToken)
      onCreated(webhook, token)
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Erro desconhecido')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="bg-white border border-lightGray rounded-lg p-5">
      <h3 className="text-sm font-bold text-dark mb-4">Novo Webhook</h3>

      <div className="space-y-3">
        <Input
          label="Flow ID *"
          value={flowId}
          onChange={e => setFlowId(e.target.value)}
          placeholder="wf_approval_v1"
          required
        />
        <Input
          label="Descrição"
          value={desc}
          onChange={e => setDesc(e.target.value)}
          placeholder="Salesforce opportunity trigger"
        />

        <div>
          <label className="text-xs font-semibold text-gray uppercase tracking-wide block mb-1">
            Context Override (JSON)
          </label>
          <textarea
            value={ctxRaw}
            onChange={e => validateCtx(e.target.value)}
            rows={3}
            className={`w-full border rounded px-3 py-2 text-sm font-mono bg-white text-dark resize-y ${
              ctxErr ? 'border-red' : 'border-lightGray'
            }`}
          />
          {ctxErr && <p className="text-xs text-red mt-1">JSON inválido</p>}
        </div>

        {error && <p className="text-xs text-red">{error}</p>}

        <div className="flex justify-end gap-2 pt-1">
          <Button variant="ghost" onClick={onCancel}>Cancelar</Button>
          <Button
            variant="primary"
            onClick={submit}
            disabled={saving || !flowId.trim() || ctxErr}
          >
            {saving ? 'Criando…' : 'Criar Webhook'}
          </Button>
        </div>
      </div>
    </div>
  )
}

// ─── DeliveryLog ──────────────────────────────────────────────────────────────

function DeliveryLog({ deliveries, loading }: { deliveries: WebhookDelivery[]; loading: boolean }) {
  if (loading) return <Spinner />
  if (deliveries.length === 0) return (
    <p className="text-xs text-gray italic">Nenhuma entrega ainda.</p>
  )
  return (
    <div className="space-y-0">
      {deliveries.map(d => (
        <div key={d.id} className="flex items-center gap-3 py-2 border-b border-lightGray text-xs">
          <HttpBadge code={d.status_code} />
          <span className="text-gray min-w-[130px]">
            {new Date(d.triggered_at).toLocaleString('pt-BR')}
          </span>
          {d.latency_ms !== null && <span className="text-gray">{d.latency_ms} ms</span>}
          {d.instance_id && (
            <code className="text-gray text-[10px]">inst …{d.instance_id.slice(-8)}</code>
          )}
          {d.error && (
            <span className="text-red flex-1 truncate">{d.error}</span>
          )}
        </div>
      ))}
    </div>
  )
}

// ─── WebhookDetail ────────────────────────────────────────────────────────────

function WebhookDetail({ webhook, adminToken, onUpdated, onDeleted, plainToken, clearToken }: {
  webhook:    Webhook
  adminToken: string
  onUpdated:  (updated: Webhook) => void
  onDeleted:  () => void
  plainToken: string | null
  clearToken: () => void
}) {
  const { deliveries, loading: dlLoading, refresh: refreshDl } = useWebhookDeliveries(webhook.id, adminToken)
  const [confirming, setConfirming] = useState<'delete' | 'rotate' | null>(null)
  const [working,    setWorking]    = useState(false)
  const [error,      setError]      = useState('')
  const [localToken, setLocalToken] = useState<string | null>(plainToken)

  React.useEffect(() => { if (plainToken) setLocalToken(plainToken) }, [plainToken])

  async function toggleActive() {
    setWorking(true); setError('')
    try {
      const updated = await patchWebhookApi(webhook.id, { active: !webhook.active }, adminToken)
      onUpdated(updated)
    } catch (e: unknown) { setError(e instanceof Error ? e.message : 'Erro') }
    finally { setWorking(false) }
  }

  async function rotate() {
    setWorking(true); setError(''); setConfirming(null)
    try {
      const { webhook: updated, token } = await rotateWebhookTokenApi(webhook.id, adminToken)
      setLocalToken(token)
      onUpdated(updated)
    } catch (e: unknown) { setError(e instanceof Error ? e.message : 'Erro') }
    finally { setWorking(false) }
  }

  async function doDelete() {
    setWorking(true); setError(''); setConfirming(null)
    try {
      await deleteWebhookApi(webhook.id, adminToken)
      onDeleted()
    } catch (e: unknown) { setError(e instanceof Error ? e.message : 'Erro') }
    finally { setWorking(false) }
  }

  const fmt = (iso: string | null) => iso ? new Date(iso).toLocaleString('pt-BR') : '—'
  const triggerUrl = `${window.location.origin}/v1/workflow/webhook/${webhook.id}`

  return (
    <div className="space-y-5">
      {/* Header */}
      <div className="flex items-start justify-between">
        <div>
          <div className="text-base font-bold text-dark">{webhook.flow_id}</div>
          {webhook.description && (
            <div className="text-sm text-gray mt-0.5">{webhook.description}</div>
          )}
          <div className="mt-1.5"><ActiveBadge active={webhook.active} /></div>
        </div>
        <div className="flex gap-2 flex-wrap justify-end">
          <Button variant="ghost" onClick={toggleActive} disabled={working}>
            {webhook.active ? 'Desativar' : 'Ativar'}
          </Button>
          <Button variant="ghost" onClick={() => setConfirming('rotate')} disabled={working}>
            Rotacionar Token
          </Button>
          <Button variant="danger" onClick={() => setConfirming('delete')} disabled={working}>
            Excluir
          </Button>
        </div>
      </div>

      {error && <p className="text-xs text-red">{error}</p>}

      {/* Confirmação rotate */}
      {confirming === 'rotate' && (
        <div className="border border-primary rounded-lg p-4 bg-primary/5 text-sm text-dark">
          Rotacionar o token invalida imediatamente o token atual. Continuar?
          <div className="flex gap-2 mt-3">
            <Button variant="ghost" onClick={() => setConfirming(null)}>Cancelar</Button>
            <Button variant="primary" onClick={rotate}>Rotacionar</Button>
          </div>
        </div>
      )}

      {/* Confirmação delete */}
      {confirming === 'delete' && (
        <div className="border border-red rounded-lg p-4 bg-red/5 text-sm text-dark">
          Excluir este webhook? Todos os registros de entrega serão removidos. Isso não pode ser desfeito.
          <div className="flex gap-2 mt-3">
            <Button variant="ghost" onClick={() => setConfirming(null)}>Cancelar</Button>
            <Button variant="danger" onClick={doDelete}>Excluir</Button>
          </div>
        </div>
      )}

      {/* Token recém-gerado */}
      {localToken && (
        <div>
          <CopyBox value={localToken} label="Novo Webhook Token" />
          <div className="mt-2 flex justify-end">
            <Button variant="ghost" onClick={() => { setLocalToken(null); clearToken() }}>
              Já salvei, fechar
            </Button>
          </div>
        </div>
      )}

      {/* Metadata */}
      <div className="border border-lightGray rounded-lg p-4 grid grid-cols-2 gap-x-6 gap-y-3">
        <MetaField label="Webhook ID"      value={webhook.id} mono />
        <MetaField label="Prefixo do Token" value={webhook.token_prefix} mono />
        <MetaField label="Disparos"        value={String(webhook.trigger_count)} />
        <MetaField label="Último disparo"  value={fmt(webhook.last_triggered_at)} />
        <MetaField label="Criado em"       value={fmt(webhook.created_at)} />
        <MetaField label="Atualizado em"   value={fmt(webhook.updated_at)} />
        <div className="col-span-2">
          <MetaField label="URL de disparo" value={triggerUrl} mono copyable />
        </div>
        {Object.keys(webhook.context_override).length > 0 && (
          <div className="col-span-2">
            <MetaField
              label="Context Override"
              value={JSON.stringify(webhook.context_override, null, 2)}
              mono
            />
          </div>
        )}
      </div>

      {/* Delivery log */}
      <div className="border border-lightGray rounded-lg p-4">
        <div className="flex items-center justify-between mb-3">
          <span className="text-sm font-semibold text-dark">Log de entregas</span>
          <Button variant="ghost" onClick={refreshDl}>Atualizar</Button>
        </div>
        <DeliveryLog deliveries={deliveries} loading={dlLoading} />
      </div>
    </div>
  )
}

function MetaField({ label, value, mono = false, copyable = false }: {
  label: string; value: string; mono?: boolean; copyable?: boolean
}) {
  const [copied, setCopied] = useState(false)
  function copy() {
    navigator.clipboard.writeText(value).then(() => {
      setCopied(true); setTimeout(() => setCopied(false), 2000)
    })
  }
  return (
    <div>
      <div className="text-[10px] font-semibold text-gray uppercase tracking-wide mb-0.5">{label}</div>
      <div className="flex items-center gap-1.5">
        <span className={`text-xs text-dark break-all ${mono ? 'font-mono' : ''}`}>{value}</span>
        {copyable && (
          <button
            onClick={copy}
            className={`text-xs px-2 py-0.5 rounded border cursor-pointer ${
              copied ? 'border-green text-green' : 'border-gray text-gray'
            } bg-transparent`}
          >
            {copied ? '✓' : 'Copiar'}
          </button>
        )}
      </div>
    </div>
  )
}

// ─── WebhooksTab (main) ───────────────────────────────────────────────────────

export default function WebhooksTab() {
  const { session }   = useAuth()
  const tenantId      = session?.tenantId ?? ''

  const [adminToken,   setAdminToken]   = useState('')
  const [creating,     setCreating]     = useState(false)
  const [selectedId,   setSelectedId]   = useState<string | null>(null)
  const [pendingToken, setPendingToken] = useState<string | null>(null)

  const { webhooks, loading, refresh } = useWebhooks(tenantId, adminToken)
  const selected = webhooks.find(w => w.id === selectedId) ?? null

  function handleCreated(webhook: Webhook, token: string) {
    setCreating(false)
    setPendingToken(token)
    setSelectedId(webhook.id)
    refresh()
  }

  function handleUpdated(updated: Webhook) {
    refresh()
    setSelectedId(updated.id)
  }

  function handleDeleted() {
    setSelectedId(null)
    refresh()
  }

  return (
    <div className="flex flex-1 overflow-hidden">
      {/* ── Left sidebar ──────────────────────────────────────────────────── */}
      <div className="w-80 shrink-0 border-r border-lightGray flex flex-col overflow-hidden">
        {/* Toolbar */}
        <div className="flex items-center gap-3 px-4 py-3 border-b border-lightGray">
          <span className="text-sm font-bold text-dark flex-1">Webhooks</span>
          <Button
            variant="primary"
            onClick={() => { setCreating(true); setSelectedId(null) }}
          >
            + Novo
          </Button>
        </div>

        {/* Admin token */}
        <div className="px-4 py-2.5 border-b border-lightGray">
          <input
            type="password"
            value={adminToken}
            onChange={e => setAdminToken(e.target.value)}
            placeholder="Admin token (X-Admin-Token)"
            className="w-full border border-lightGray rounded px-3 py-1.5 text-xs font-mono bg-white text-dark outline-none"
          />
        </div>

        {/* List */}
        <div className="flex-1 overflow-y-auto">
          {loading && !webhooks.length && (
            <div className="p-4"><Spinner /></div>
          )}
          {!loading && !webhooks.length && adminToken && (
            <p className="p-4 text-sm text-gray italic">Nenhum webhook cadastrado.</p>
          )}
          {!adminToken && (
            <p className="p-4 text-xs text-gray italic">Informe o admin token para gerenciar webhooks.</p>
          )}

          {webhooks.map(wh => (
            <button
              key={wh.id}
              onClick={() => { setSelectedId(wh.id); setCreating(false) }}
              className={`w-full text-left px-4 py-3 border-b border-lightGray cursor-pointer hover:bg-gray/5 ${
                selectedId === wh.id ? 'bg-primary/5 border-l-2 border-l-primary' : ''
              }`}
            >
              <div className="flex items-center gap-2 mb-1">
                <span className="text-sm font-semibold text-dark flex-1 truncate">{wh.flow_id}</span>
                <ActiveBadge active={wh.active} />
              </div>
              {wh.description && (
                <p className="text-xs text-gray truncate mb-1">{wh.description}</p>
              )}
              <div className="flex gap-3 text-xs text-gray">
                <span>🔗 {wh.trigger_count} disparos</span>
                <code>{wh.token_prefix}…</code>
              </div>
            </button>
          ))}
        </div>
      </div>

      {/* ── Right content ──────────────────────────────────────────────────── */}
      <div className="flex-1 overflow-y-auto p-6">
        {creating && (
          <CreateForm
            tenantId={tenantId}
            adminToken={adminToken}
            onCreated={handleCreated}
            onCancel={() => setCreating(false)}
          />
        )}

        {!creating && selected && (
          <WebhookDetail
            key={selected.id}
            webhook={selected}
            adminToken={adminToken}
            onUpdated={handleUpdated}
            onDeleted={handleDeleted}
            plainToken={selectedId === selected.id ? pendingToken : null}
            clearToken={() => setPendingToken(null)}
          />
        )}

        {!creating && !selected && (
          <div className="h-full flex flex-col items-center justify-center gap-3 text-center">
            <div className="text-4xl">🔗</div>
            <p className="text-sm font-semibold text-gray">
              Selecione um webhook ou crie um novo
            </p>
            <p className="text-xs text-gray max-w-sm">
              Webhooks permitem que sistemas externos (Salesforce, ERP, etc.)
              disparem workflows automaticamente via URL autenticada.
            </p>
          </div>
        )}
      </div>
    </div>
  )
}
