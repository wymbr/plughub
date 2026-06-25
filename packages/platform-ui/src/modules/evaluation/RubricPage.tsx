/**
 * RubricPage.tsx
 * /evaluation/rubric — Rubrica / Prompt do avaliador (T8-C, spec §16.3)
 *
 * Edita a rubrica-template (instruções gerais do avaliador): default por tenant +
 * override por campanha. Draft livre, preview do prompt composto, publish (versão imutável).
 * Backend: T8-A (storage/versões) + T8-B (composição/preview/effective).
 */
import React, { useEffect, useMemo, useState } from 'react'
import { RefreshCw, Eye, UploadCloud, Save, History } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { useAuth } from '@/auth/useAuth'
import {
  useRubricTemplates, useRubricVersions, useForms, useCampaigns,
  createRubricTemplate, updateRubricTemplate, publishRubricTemplate, previewRubric,
  type RubricTemplate, type RubricPreviewResult,
} from '@/api/evaluation-hooks'

export default function RubricPage() {
  const { t } = useTranslation('evaluation')
  const { session } = useAuth()
  const tenantId = session?.tenantId ?? ''
  const accessToken = session?.accessToken

  const [scope, setScope] = useState<'tenant' | 'campaign'>('tenant')
  const [campaignId, setCampaignId] = useState('')
  const [name, setName] = useState('')
  const [body, setBody] = useState('')
  const [formId, setFormId] = useState('')
  const [preview, setPreview] = useState<RubricPreviewResult | null>(null)
  const [showVersions, setShowVersions] = useState(false)
  const [msg, setMsg] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const { forms } = useForms(tenantId, accessToken)
  const { campaigns } = useCampaigns(tenantId, 0, accessToken)
  const activeCampaign = scope === 'campaign' ? campaignId : undefined
  const { templates, loading, reload } = useRubricTemplates(tenantId, activeCampaign, accessToken)

  const current: RubricTemplate | undefined = useMemo(() => {
    if (scope === 'tenant') return templates.find(t => t.scope === 'tenant')
    return templates.find(t => t.scope === 'campaign' && t.campaign_id === campaignId)
  }, [templates, scope, campaignId])

  // Sincroniza o editor quando a rubrica corrente muda (troca de escopo/campanha/reload)
  useEffect(() => {
    setName(current?.name ?? (scope === 'tenant' ? 'Rubrica padrão' : 'Override da campanha'))
    setBody(current?.body ?? '')
    setPreview(null)
  }, [current?.id, scope, campaignId]) // eslint-disable-line react-hooks/exhaustive-deps

  const { versions, reload: reloadVersions } = useRubricVersions(current?.id ?? null, tenantId)

  const flash = (m: string) => { setMsg(m); setTimeout(() => setMsg(null), 4000) }

  const handleSave = async () => {
    if (scope === 'campaign' && !campaignId) { flash(t('rubric.pickCampaign', { defaultValue: 'Selecione uma campanha.' })); return }
    setBusy(true)
    try {
      if (current) {
        await updateRubricTemplate(current.id, tenantId, { name, body }, accessToken)
      } else {
        await createRubricTemplate(tenantId, {
          scope, campaign_id: scope === 'campaign' ? campaignId : undefined, name, body,
        }, accessToken)
      }
      await reload()
      flash(t('rubric.saved', { defaultValue: 'Rascunho salvo.' }))
    } catch (e) { flash(String(e)) } finally { setBusy(false) }
  }

  const handlePublish = async () => {
    if (!current) { flash(t('rubric.saveFirst', { defaultValue: 'Salve antes de publicar.' })); return }
    setBusy(true)
    try {
      await updateRubricTemplate(current.id, tenantId, { name, body }, accessToken) // garante o body atual
      await publishRubricTemplate(current.id, tenantId, accessToken)
      await reload(); await reloadVersions()
      flash(t('rubric.published', { defaultValue: 'Publicado (versão congelada).' }))
    } catch (e) { flash(String(e)) } finally { setBusy(false) }
  }

  const handlePreview = async () => {
    setBusy(true)
    try {
      const r = await previewRubric(tenantId, {
        form_id: formId || undefined,
        campaign_id: scope === 'campaign' ? campaignId || undefined : undefined,
        rubric_body: body,
      })
      setPreview(r)
    } catch (e) { flash(String(e)) } finally { setBusy(false) }
  }

  const statusBadge = current
    ? <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${current.deploy_status === 'published' ? 'bg-green-light text-green-text' : 'bg-surface-alt text-muted'}`}>
        {current.deploy_status === 'published' ? t('rubric.published_badge', { defaultValue: 'Publicada' }) : t('rubric.draft_badge', { defaultValue: 'Rascunho' })} · v{current.version}
      </span>
    : <span className="text-xs px-2 py-0.5 rounded-full bg-surface-alt text-muted">{t('rubric.new', { defaultValue: 'Nova' })}</span>

  return (
    <div className="p-6 max-w-5xl mx-auto space-y-6">
      {/* Header */}
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-xl font-semibold text-dark">{t('rubric.title', { defaultValue: 'Rubrica / Prompt' })}</h1>
          <p className="text-sm text-muted mt-0.5">{t('rubric.subtitle', { defaultValue: 'Instruções gerais do avaliador — default do tenant ou override por campanha.' })}</p>
        </div>
        <button onClick={reload} className="text-sm text-muted hover:text-dark border rounded px-3 py-1.5 inline-flex items-center gap-1">
          <RefreshCw className="w-4 h-4" aria-hidden="true" /> {t('rubric.refresh', { defaultValue: 'Recarregar' })}
        </button>
      </div>

      {msg && <div className="bg-primary-light border border-primary/30 text-primary rounded p-2 text-sm">{msg}</div>}

      {/* Escopo */}
      <div className="flex flex-wrap gap-3 items-center">
        <div className="inline-flex rounded border overflow-hidden">
          {(['tenant', 'campaign'] as const).map(s => (
            <button key={s} onClick={() => setScope(s)}
              className={`px-3 py-1.5 text-sm ${scope === s ? 'bg-primary text-white' : 'bg-white text-muted hover:bg-surface-muted'}`}>
              {s === 'tenant' ? t('rubric.scope.tenant', { defaultValue: 'Default do tenant' }) : t('rubric.scope.campaign', { defaultValue: 'Override de campanha' })}
            </button>
          ))}
        </div>
        {scope === 'campaign' && (
          <select value={campaignId} onChange={e => setCampaignId(e.target.value)}
            className="border rounded px-3 py-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-primary/40 min-w-[16rem]">
            <option value="">{t('rubric.pickCampaign', { defaultValue: 'Selecione uma campanha…' })}</option>
            {campaigns.map(c => <option key={c.campaign_id} value={c.campaign_id}>{c.name}</option>)}
          </select>
        )}
        <div className="ml-auto">{statusBadge}</div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Editor */}
        <div className="space-y-3">
          <label className="block">
            <span className="text-sm font-medium text-dark">{t('rubric.nameLabel', { defaultValue: 'Nome' })}</span>
            <input type="text" value={name} onChange={e => setName(e.target.value)}
              className="w-full border rounded px-3 py-2 text-sm mt-1 focus:outline-none focus:ring-1 focus:ring-primary/40" />
          </label>
          <label className="block">
            <span className="text-sm font-medium text-dark">{t('rubric.bodyLabel', { defaultValue: 'Instruções gerais (rubrica)' })}</span>
            <p className="text-xs text-muted-light mb-1">{t('rubric.bodyHint', { defaultValue: 'Como pontuar (0/5/10), citar evidência por stream_entry_id, N/A, anti-viés. Vazio = usa a rubrica built-in.' })}</p>
            <textarea value={body} onChange={e => setBody(e.target.value)} rows={16}
              className="w-full border rounded px-3 py-2 text-sm font-mono resize-y focus:outline-none focus:ring-1 focus:ring-primary/40"
              placeholder={t('rubric.bodyPlaceholder', { defaultValue: 'Ex.: Avalie cada critério na sua escala…' })} />
          </label>
          <div className="flex flex-wrap gap-2">
            <button onClick={handleSave} disabled={busy}
              className="px-4 py-2 text-sm rounded bg-primary text-white font-medium hover:bg-primary/90 disabled:opacity-40 inline-flex items-center gap-1">
              <Save className="w-4 h-4" aria-hidden="true" /> {t('rubric.save', { defaultValue: 'Salvar rascunho' })}
            </button>
            <button onClick={handlePublish} disabled={busy || !current}
              className="px-4 py-2 text-sm rounded border border-green/40 text-green-text font-medium hover:bg-green-light disabled:opacity-40 inline-flex items-center gap-1">
              <UploadCloud className="w-4 h-4" aria-hidden="true" /> {t('rubric.publish', { defaultValue: 'Publicar' })}
            </button>
            <button onClick={() => { setShowVersions(v => !v); reloadVersions() }} disabled={!current}
              className="px-3 py-2 text-sm rounded border text-muted hover:text-dark disabled:opacity-40 inline-flex items-center gap-1">
              <History className="w-4 h-4" aria-hidden="true" /> {t('rubric.versions', { defaultValue: 'Versões' })} ({versions.length})
            </button>
          </div>
          {showVersions && (
            <div className="border rounded divide-y text-sm">
              {versions.length === 0 && <div className="p-2 text-muted-light">{t('rubric.noVersions', { defaultValue: 'Nenhuma versão publicada.' })}</div>}
              {versions.map(v => (
                <div key={v.version} className="p-2 flex items-center justify-between">
                  <span className="font-medium">v{v.version}</span>
                  <span className="text-xs text-muted-light">{new Date(v.published_at).toLocaleString('pt-BR')} · {v.published_by}</span>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Preview */}
        <div className="space-y-3">
          <div className="flex items-center gap-2">
            <select value={formId} onChange={e => setFormId(e.target.value)}
              className="border rounded px-3 py-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-primary/40 flex-1 min-w-0">
              <option value="">{t('rubric.pickForm', { defaultValue: 'Formulário de exemplo (opcional)…' })}</option>
              {forms.map(f => {
                const id = (f as any).form_id ?? (f as any).id
                return <option key={id} value={id}>{f.name}</option>
              })}
            </select>
            <button onClick={handlePreview} disabled={busy}
              className="px-4 py-2 text-sm rounded bg-secondary text-white font-medium hover:bg-secondary/90 disabled:opacity-40 inline-flex items-center gap-1">
              <Eye className="w-4 h-4" aria-hidden="true" /> {t('rubric.preview', { defaultValue: 'Preview' })}
            </button>
          </div>
          {preview ? (
            <div className="border rounded bg-surface-muted">
              <div className="px-3 py-2 border-b text-xs text-muted flex items-center justify-between">
                <span>{t('rubric.previewSource', { defaultValue: 'Origem' })}: <b>{preview.rubric_source}</b></span>
                <span>{t('rubric.previewCounts', { defaultValue: 'critérios' })}: {preview.criteria_count} · calib: {preview.calibration_notes_count}</span>
              </div>
              <pre className="p-3 text-xs whitespace-pre-wrap font-mono max-h-[28rem] overflow-y-auto">{preview.composed_prompt}</pre>
            </div>
          ) : (
            <div className="border rounded border-dashed p-8 text-center text-sm text-muted-light">
              {t('rubric.previewEmpty', { defaultValue: 'Clique em Preview para ver o prompt composto que o avaliador recebe.' })}
            </div>
          )}
        </div>
      </div>

      {loading && <p className="text-sm text-muted-light">{t('rubric.loading', { defaultValue: 'Carregando…' })}</p>}
    </div>
  )
}
