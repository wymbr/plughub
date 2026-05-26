/**
 * KnowledgePage.tsx
 * /evaluation/knowledge — Knowledge base management (search, add, delete snippets)
 */

import React, { useState } from 'react'
import { Search, BookOpen, X } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { searchKnowledge, upsertSnippet, deleteSnippet } from '@/api/evaluation-hooks'
import type { KnowledgeSnippet } from '@/types'
import { useAuth } from '@/auth/useAuth'

const NAMESPACES = [
  'evaluation_policies',
  'greeting_scripts',
  'product_knowledge',
  'compliance_rules',
  'escalation_criteria',
  'quality_standards',
]

function SnippetCard({
  snippet,
  onDelete,
}: {
  snippet: KnowledgeSnippet
  onDelete: (id: string) => void
}) {
  const { t } = useTranslation('evaluation')
  const [confirmDelete, setConfirmDelete] = useState(false)
  const scoreColor = (snippet.score ?? 0) >= 0.7 ? 'text-green-text' : (snippet.score ?? 0) >= 0.4 ? 'text-warning-text' : 'text-muted-light'

  return (
    <div className="bg-white border rounded p-4 space-y-2 hover:shadow-sm transition-shadow">
      <div className="flex items-start justify-between gap-2">
        <div className="flex-1">
          <div className="flex items-center gap-2 mb-1">
            <span className="bg-primary-light text-primary text-xs px-2 py-0.5 rounded font-medium">
              {snippet.namespace}
            </span>
            {snippet.score !== undefined && (
              <span className={`text-xs font-medium ${scoreColor}`}>
                score: {snippet.score.toFixed(3)}
              </span>
            )}
            {snippet.source_ref && (
              <span className="text-xs text-muted-light truncate max-w-32">{snippet.source_ref}</span>
            )}
          </div>
          <p className="text-sm text-dark leading-relaxed line-clamp-4">{snippet.content}</p>
        </div>

        <div className="flex-shrink-0">
          {confirmDelete ? (
            <div className="flex gap-1">
              <button
                onClick={() => onDelete(snippet.snippet_id)}
                className="text-xs text-red-text hover:text-red-text/80 border border-red/30 rounded px-2 py-0.5"
              >
                {t('knowledge.confirmDelete')}
              </button>
              <button onClick={() => setConfirmDelete(false)} className="text-xs text-muted hover:text-dark" aria-label={t('knowledge.cancel')}>
                <X className="w-3.5 h-3.5" />
              </button>
            </div>
          ) : (
            <button
              onClick={() => setConfirmDelete(true)}
              className="text-xs text-muted-light hover:text-red"
              aria-label={t('knowledge.delete')}
            >
              🗑
            </button>
          )}
        </div>
      </div>

      {snippet.metadata && Object.keys(snippet.metadata).length > 0 && (
        <div className="flex gap-1 flex-wrap">
          {Object.entries(snippet.metadata).map(([k, v]) => (
            <span key={k} className="bg-surface-muted text-muted text-xs px-1.5 py-0.5 rounded">
              {k}: {String(v)}
            </span>
          ))}
        </div>
      )}
    </div>
  )
}

export default function KnowledgePage() {
  const { t } = useTranslation('evaluation')
  const { tenantId: TENANT } = useAuth()
  const [adminToken, setAdminToken] = useState('')
  const [namespace, setNamespace] = useState(NAMESPACES[0])
  const [customNs, setCustomNs] = useState('')
  const [query, setQuery] = useState('')
  const [snippets, setSnippets] = useState<KnowledgeSnippet[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Add snippet state
  const [showAdd, setShowAdd] = useState(false)
  const [newContent, setNewContent] = useState('')
  const [newSourceRef, setNewSourceRef] = useState('')
  const [adding, setAdding] = useState(false)

  const effectiveNs = customNs.trim() || namespace

  const doSearch = async () => {
    if (!query.trim()) return
    setLoading(true)
    setError(null)
    try {
      const results = await searchKnowledge(TENANT, query.trim(), effectiveNs, 20)
      setSnippets(results)
    } catch (e) {
      setError(String(e))
    } finally {
      setLoading(false)
    }
  }

  const doAdd = async () => {
    if (!newContent.trim()) return
    setAdding(true)
    setError(null)
    try {
      await upsertSnippet({
        tenant_id: TENANT,
        namespace: effectiveNs,
        content: newContent.trim(),
        source_ref: newSourceRef.trim() || undefined,
      }, adminToken)
      setNewContent('')
      setNewSourceRef('')
      setShowAdd(false)
      // Re-search to show updated list
      if (query.trim()) await doSearch()
    } catch (e) {
      setError(String(e))
    } finally {
      setAdding(false)
    }
  }

  const doDelete = async (snippetId: string) => {
    try {
      await deleteSnippet(snippetId, adminToken)
      setSnippets(prev => prev.filter(s => s.snippet_id !== snippetId))
    } catch (e) {
      setError(String(e))
    }
  }

  return (
    <div className="flex flex-col h-full">
      {/* Toolbar */}
      <div className="p-4 border-b bg-surface-muted space-y-3">
        <div className="flex gap-3 items-end">
          <div>
            <label className="block text-xs text-muted mb-1">{t('knowledge.namespace')}</label>
            <div className="flex gap-2">
              <select
                className="border border-border-strong rounded px-2 py-1.5 text-sm"
                value={customNs ? '__custom__' : namespace}
                onChange={e => {
                  if (e.target.value === '__custom__') setCustomNs('')
                  else { setNamespace(e.target.value); setCustomNs('') }
                }}
              >
                {NAMESPACES.map(ns => <option key={ns} value={ns}>{ns}</option>)}
                <option value="__custom__">{t('knowledge.customNs')}</option>
              </select>
              {customNs !== undefined && (namespace === '__custom__' || customNs) && (
                <input
                  className="border border-border-strong rounded px-2 py-1.5 text-sm w-48"
                  placeholder={t('knowledge.customNsPlaceholder')}
                  value={customNs}
                  onChange={e => setCustomNs(e.target.value)}
                />
              )}
            </div>
          </div>

          <div className="flex-1">
            <label className="block text-xs text-muted mb-1">{t('knowledge.searchLabel')}</label>
            <div className="flex gap-2">
              <input
                className="flex-1 border border-border-strong rounded px-3 py-1.5 text-sm"
                placeholder={t('knowledge.searchPlaceholder')}
                value={query}
                onChange={e => setQuery(e.target.value)}
                onKeyDown={e => e.key === 'Enter' && doSearch()}
              />
              <button
                onClick={doSearch}
                disabled={loading}
                className="bg-primary text-white text-sm px-4 py-1.5 rounded hover:bg-primary-dark disabled:opacity-50"
              >
                {loading ? '…' : t('knowledge.searchBtn')}
              </button>
            </div>
          </div>

          <div className="flex flex-col gap-1">
            <label className="block text-xs text-muted">{t('knowledge.adminToken')}</label>
            <div className="flex gap-2">
              <input
                type="password"
                className="border border-border-strong rounded px-2 py-1.5 text-sm w-32"
                placeholder="Token"
                value={adminToken}
                onChange={e => setAdminToken(e.target.value)}
              />
              <button
                onClick={() => setShowAdd(v => !v)}
                className="bg-green text-white text-sm px-3 py-1.5 rounded hover:bg-green-text"
              >
                {t('knowledge.addSnippet')}
              </button>
            </div>
          </div>
        </div>

        {/* Add snippet form */}
        {showAdd && (
          <div className="bg-white border rounded p-3 space-y-2">
            <div className="text-xs font-semibold text-muted">{t('knowledge.addSnippetTitle', { namespace: effectiveNs })}</div>
            <textarea
              className="w-full border border-border-strong rounded px-3 py-2 text-sm resize-none"
              rows={4}
              placeholder={t('knowledge.contentPlaceholder')}
              value={newContent}
              onChange={e => setNewContent(e.target.value)}
            />
            <div className="flex gap-2 items-center">
              <input
                className="flex-1 border border-border-strong rounded px-2 py-1 text-sm"
                placeholder={t('knowledge.sourceRefPlaceholder')}
                value={newSourceRef}
                onChange={e => setNewSourceRef(e.target.value)}
              />
              <button
                onClick={doAdd}
                disabled={adding || !newContent.trim()}
                className="bg-green text-white text-sm px-3 py-1.5 rounded hover:bg-green-text disabled:opacity-50"
              >
                {adding ? t('knowledge.saving') : t('knowledge.save')}
              </button>
              <button onClick={() => setShowAdd(false)} className="text-xs text-muted hover:text-dark px-2">
                {t('knowledge.cancel')}
              </button>
            </div>
          </div>
        )}
      </div>

      {error && <div className="bg-red-light text-red-text text-sm px-4 py-2">{error}</div>}

      {/* Results */}
      <div className="flex-1 overflow-y-auto p-4">
        {loading && (
          <div className="text-center text-muted-light py-8">{t('knowledge.searching')}</div>
        )}

        {!loading && snippets.length === 0 && query && (
          <div className="text-center text-muted-light py-8">
            <div className="mb-2 flex justify-center"><Search className="w-8 h-8 text-muted-light" aria-hidden="true" /></div>
            <p>{t('knowledge.noResultsQuery', { query, namespace: effectiveNs })}</p>
            <p className="text-xs mt-1">{t('knowledge.noResultsTip')}</p>
          </div>
        )}

        {!loading && snippets.length === 0 && !query && (
          <div className="text-center text-muted-light py-8">
            <div className="mb-2 flex justify-center"><BookOpen className="w-10 h-10 text-muted-light" aria-hidden="true" /></div>
            <p>{t('knowledge.emptyState')}</p>
            <p className="text-xs mt-1 text-border-strong">{t('knowledge.emptyStateSub')}</p>
          </div>
        )}

        <div className="space-y-3">
          {snippets.map(s => (
            <SnippetCard key={s.snippet_id} snippet={s} onDelete={doDelete} />
          ))}
        </div>

        {snippets.length > 0 && (
          <p className="text-xs text-muted-light text-center mt-4">
            {t('knowledge.foundCount', { count: snippets.length })}
          </p>
        )}
      </div>
    </div>
  )
}
