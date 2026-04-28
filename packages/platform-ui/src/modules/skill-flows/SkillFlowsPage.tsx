/**
 * SkillFlowsPage.tsx
 * Monaco-based YAML editor for SkillFlow definitions.
 * Migrated from packages/operator-console/src/components/SkillFlowEditor.tsx
 *
 * Layout:
 *   ┌──────────────────────────────────────────────────────┐
 *   │  Page header (title + keyboard hint)                 │
 *   ├──────────────┬───────────────────────────────────────┤
 *   │  Skill list  │  Monaco YAML editor                   │
 *   │  search      │                                       │
 *   │  + New       │  ─────────────────────────────────    │
 *   │  rows…       │  Status bar                           │
 *   └──────────────┴───────────────────────────────────────┘
 *
 * Skills are stored as JSON in the Agent Registry API and displayed as YAML.
 * On save, the YAML is parsed back to JSON and PUT to /v1/skills/:id.
 */
import React, { useCallback, useEffect, useRef, useState } from 'react'
import Editor from '@monaco-editor/react'
import * as yaml from 'js-yaml'
import { useAuth } from '@/auth/useAuth'
import Spinner from '@/components/ui/Spinner'
import type { Skill } from '@/types'

// ── Blank template ──────────────────────────────────────────────────────────

const BLANK_TEMPLATE = `# New Skill — fill in the required fields below
skill_id: skill_novo_v1
name: "New Skill"
version: "1.0"
description: "Describe what this skill does."

classification:
  type: horizontal       # vertical | horizontal | orchestrator
  domain: general

tools: []
knowledge_domains: []

# flow: required only for orchestrator skills (entry + steps)
# flow:
#   entry: inicio
#   steps:
#     - id: inicio
#       type: notify
#       message: "Hello"
#       on_success: fim
#     - id: fim
#       type: complete
#       outcome: resolved
`

// ── Helpers ──────────────────────────────────────────────────────────────────

function skillToYaml(obj: Record<string, unknown>): string {
  // Strip server-added fields before displaying
  const { tenant_id, created_at, updated_at, created_by, status, ...rest } = obj
  void tenant_id; void created_at; void updated_at; void created_by; void status
  try {
    return yaml.dump(rest, { lineWidth: 100, indent: 2, noRefs: true })
  } catch {
    return JSON.stringify(rest, null, 2)
  }
}

type ParseResult =
  | { ok: true;  data: Record<string, unknown> }
  | { ok: false; error: string }

function yamlToJson(text: string): ParseResult {
  try {
    const parsed = yaml.load(text)
    if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) {
      return { ok: false, error: 'YAML must be a mapping (object), not a list or scalar.' }
    }
    return { ok: true, data: parsed as Record<string, unknown> }
  } catch (e: unknown) {
    return { ok: false, error: e instanceof Error ? e.message : 'YAML parse error' }
  }
}

const operatorHeaders = (tenantId: string) => ({
  'Content-Type': 'application/json',
  'x-tenant-id': tenantId,
  'x-user-id': 'operator',
})

async function apiFetchRaw(url: string, init?: RequestInit): Promise<Record<string, unknown>> {
  const res = await fetch(url, init)
  if (!res.ok) {
    const body = await res.json().catch(() => ({})) as { detail?: string; error?: string }
    throw new Error(body.detail ?? body.error ?? `HTTP ${res.status}`)
  }
  return res.json() as Promise<Record<string, unknown>>
}

// ── Status bar ───────────────────────────────────────────────────────────────

type StatusKind = 'idle' | 'loading' | 'saving' | 'saved' | 'error' | 'parse_error'

const STATUS_BG: Record<StatusKind, string> = {
  idle:        'bg-gray-900',
  loading:     'bg-gray-900',
  saving:      'bg-indigo-950',
  saved:       'bg-green-950',
  error:       'bg-red-950',
  parse_error: 'bg-red-950',
}

const STATUS_COLOR: Record<StatusKind, string> = {
  idle:        'text-gray-500',
  loading:     'text-gray-400',
  saving:      'text-violet-400',
  saved:       'text-green-400',
  error:       'text-red-400',
  parse_error: 'text-red-400',
}

const STATUS_ICON: Record<StatusKind, string> = {
  idle:        '',
  loading:     '⏳',
  saving:      '💾',
  saved:       '✓',
  error:       '✗',
  parse_error: '✗',
}

function StatusBar({ kind, message }: { kind: StatusKind; message: string }) {
  return (
    <div className={`h-7 shrink-0 border-t border-gray-800 flex items-center px-4 gap-2 text-xs font-mono ${STATUS_BG[kind]} ${STATUS_COLOR[kind]}`}>
      {STATUS_ICON[kind] && <span>{STATUS_ICON[kind]}</span>}
      <span>{message}</span>
    </div>
  )
}

// ── Skill list item ───────────────────────────────────────────────────────────

type ClassificationAware = Skill & {
  classification?: { type?: string; domain?: string }
}

function SkillListItem({
  skill, selected, modified, onClick,
}: {
  skill: ClassificationAware
  selected: boolean
  modified: boolean
  onClick: () => void
}) {
  const typeColor = skill.classification?.type === 'orchestrator'
    ? 'text-violet-400'
    : skill.classification?.type === 'vertical'
    ? 'text-cyan-400'
    : 'text-yellow-400'

  return (
    <div
      onClick={onClick}
      className={`cursor-pointer border-b border-gray-800 px-3 py-2.5 transition-colors hover:bg-gray-800/60 ${
        selected ? 'bg-gray-800 border-l-2 border-l-violet-500' : 'border-l-2 border-l-transparent'
      }`}
    >
      <div className="flex items-center gap-1.5 mb-0.5">
        {modified && <span className="text-yellow-400 text-xs font-bold">●</span>}
        <span className="text-xs font-semibold text-gray-100 font-mono truncate">
          {skill.skill_id}
        </span>
      </div>
      <div className="flex items-center gap-2">
        <span className={`text-xs ${typeColor}`}>{skill.classification?.type ?? '—'}</span>
        <span className="text-xs text-gray-500">v{skill.version}</span>
        {skill.status !== 'active' && (
          <span className="text-xs text-gray-500">{skill.status}</span>
        )}
      </div>
    </div>
  )
}

// ── New Skill prompt ──────────────────────────────────────────────────────────

function NewSkillPrompt({
  onConfirm, onCancel,
}: { onConfirm: (id: string) => void; onCancel: () => void }) {
  const [val, setVal] = useState('skill_novo_v1')

  return (
    <div className="px-3 py-3 border-b border-gray-800 bg-violet-950/50">
      <p className="text-xs font-bold text-violet-400 mb-2">Skill ID</p>
      <input
        value={val}
        onChange={e => setVal(e.target.value)}
        onKeyDown={e => { if (e.key === 'Enter') onConfirm(val) }}
        placeholder="skill_name_v1"
        className="w-full px-2 py-1.5 text-xs border border-violet-700/50 rounded bg-gray-800 text-gray-100 font-mono focus:outline-none focus:border-violet-500 placeholder-gray-500"
      />
      <div className="flex gap-2 mt-2">
        <button
          onClick={() => onConfirm(val)}
          className="px-3 py-1 text-xs font-bold rounded border border-violet-500 bg-violet-950 text-violet-400 hover:bg-violet-900 transition-colors"
        >
          Criar
        </button>
        <button
          onClick={onCancel}
          className="px-3 py-1 text-xs rounded border border-gray-700 text-gray-400 hover:bg-gray-800 transition-colors"
        >
          Cancelar
        </button>
      </div>
    </div>
  )
}

// ── SkillFlowsPage ────────────────────────────────────────────────────────────

const SkillFlowsPage: React.FC = () => {
  const { session } = useAuth()
  const tenantId = session?.tenantId ?? ''

  // ── Skill list state ───────────────────────────────────────────────────────
  const [skills,      setSkills]      = useState<Skill[]>([])
  const [listLoading, setListLoading] = useState(false)

  const refreshList = useCallback(async () => {
    if (!tenantId) return
    setListLoading(true)
    try {
      const res = await fetch('/v1/skills', { headers: operatorHeaders(tenantId) })
      if (res.ok) {
        const data = await res.json() as { items?: Skill[]; skills?: Skill[] }
        setSkills(data.items ?? data.skills ?? [])
      }
    } catch { /* stale ok */ }
    finally { setListLoading(false) }
  }, [tenantId])

  useEffect(() => {
    void refreshList()
    const id = setInterval(() => void refreshList(), 30_000)
    return () => clearInterval(id)
  }, [refreshList])

  // ── Editor state ───────────────────────────────────────────────────────────
  const [selectedId,   setSelectedId]   = useState<string | null>(null)
  const [editorValue,  setEditorValue]  = useState(BLANK_TEMPLATE)
  const [savedValue,   setSavedValue]   = useState(BLANK_TEMPLATE)
  const [statusKind,   setStatusKind]   = useState<StatusKind>('idle')
  const [statusMsg,    setStatusMsg]    = useState('Selecione uma skill ou crie uma nova')
  const [isNew,        setIsNew]        = useState(false)
  const [newPrompt,    setNewPrompt]    = useState(false)
  const [search,       setSearch]       = useState('')
  const [confirmDel,   setConfirmDel]   = useState(false)

  const isModified = editorValue !== savedValue
  const editorRef  = useRef<unknown>(null)

  // ── Load skill ─────────────────────────────────────────────────────────────
  const loadSkill = useCallback(async (skillId: string) => {
    setStatusKind('loading')
    setStatusMsg(`Carregando ${skillId}…`)
    setConfirmDel(false)
    try {
      const data = await apiFetchRaw(`/v1/skills/${encodeURIComponent(skillId)}`, {
        headers: operatorHeaders(tenantId),
      })
      const yamlText = skillToYaml(data)
      setEditorValue(yamlText)
      setSavedValue(yamlText)
      setIsNew(false)
      setStatusKind('idle')
      setStatusMsg(`Carregado: ${skillId}`)
    } catch (e: unknown) {
      setStatusKind('error')
      setStatusMsg(e instanceof Error ? e.message : 'Falha ao carregar skill')
    }
  }, [tenantId])

  function selectSkill(skillId: string) {
    if (isModified && !confirm('Descartar alterações não salvas?')) return
    setSelectedId(skillId)
    void loadSkill(skillId)
  }

  // ── New skill ──────────────────────────────────────────────────────────────
  function handleNewConfirm(skillId: string) {
    if (isModified && !confirm('Descartar alterações não salvas?')) return
    const template = BLANK_TEMPLATE.replace('skill_novo_v1', skillId)
    setSelectedId(skillId)
    setEditorValue(template)
    setSavedValue('')   // force isModified = true
    setIsNew(true)
    setNewPrompt(false)
    setStatusKind('idle')
    setStatusMsg(`Nova skill: ${skillId} (não salva)`)
  }

  // ── Save ───────────────────────────────────────────────────────────────────
  async function handleSave() {
    const parseResult = yamlToJson(editorValue)
    if (!parseResult.ok) {
      setStatusKind('parse_error')
      setStatusMsg(`Erro de YAML: ${parseResult.error}`)
      return
    }
    const payload = parseResult.data
    const skillId = (payload.skill_id as string | undefined) || selectedId
    if (!skillId) {
      setStatusKind('error')
      setStatusMsg('skill_id é obrigatório no YAML.')
      return
    }

    setStatusKind('saving')
    setStatusMsg(`Salvando ${skillId}…`)
    try {
      await apiFetchRaw(`/v1/skills/${encodeURIComponent(skillId)}`, {
        method:  'PUT',
        headers: operatorHeaders(tenantId),
        body:    JSON.stringify(payload),
      })
      setSavedValue(editorValue)
      setSelectedId(skillId)
      setIsNew(false)
      setStatusKind('saved')
      setStatusMsg(`Salvo: ${skillId}`)
      void refreshList()
    } catch (e: unknown) {
      setStatusKind('error')
      setStatusMsg(e instanceof Error ? e.message : 'Erro ao salvar')
    }
  }

  // ── Delete ─────────────────────────────────────────────────────────────────
  async function handleDelete() {
    if (!selectedId) return
    try {
      const res = await fetch(`/v1/skills/${encodeURIComponent(selectedId)}`, {
        method:  'DELETE',
        headers: operatorHeaders(tenantId),
      })
      if (!res.ok) {
        const body = await res.json().catch(() => ({})) as { detail?: string }
        throw new Error(body.detail ?? `HTTP ${res.status}`)
      }
      setSelectedId(null)
      setEditorValue(BLANK_TEMPLATE)
      setSavedValue(BLANK_TEMPLATE)
      setIsNew(false)
      setConfirmDel(false)
      setStatusKind('idle')
      setStatusMsg('Skill removida')
      void refreshList()
    } catch (e: unknown) {
      setStatusKind('error')
      setStatusMsg(e instanceof Error ? e.message : 'Erro ao remover')
    }
  }

  // ── Discard ────────────────────────────────────────────────────────────────
  function handleDiscard() {
    if (!confirm('Descartar todas as alterações não salvas?')) return
    if (isNew) {
      setSelectedId(null)
      setEditorValue(BLANK_TEMPLATE)
      setSavedValue(BLANK_TEMPLATE)
      setIsNew(false)
      setStatusKind('idle')
      setStatusMsg('Nenhuma skill selecionada')
    } else if (selectedId) {
      void loadSkill(selectedId)
    }
  }

  // ── Live YAML validation ───────────────────────────────────────────────────
  function handleEditorChange(value: string | undefined) {
    const v = value ?? ''
    setEditorValue(v)
    if (!v.trim()) return
    const result = yamlToJson(v)
    if (!result.ok) {
      setStatusKind('parse_error')
      setStatusMsg(result.error)
    } else if (statusKind === 'parse_error') {
      setStatusKind('idle')
      setStatusMsg(selectedId ? `Editando: ${selectedId}` : 'Nova skill')
    }
  }

  // ── Keyboard shortcut ⌘S ──────────────────────────────────────────────────
  useEffect(() => {
    function onKeyDown(e: KeyboardEvent) {
      if ((e.ctrlKey || e.metaKey) && e.key === 's') {
        e.preventDefault()
        void handleSave()
      }
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  })

  // ── Filtered list ──────────────────────────────────────────────────────────
  const filteredSkills = skills.filter(s =>
    !search ||
    s.skill_id.toLowerCase().includes(search.toLowerCase()) ||
    s.name.toLowerCase().includes(search.toLowerCase())
  )

  // ── Render ─────────────────────────────────────────────────────────────────
  return (
    <div className="flex flex-col h-full bg-gray-950 text-gray-100">

      {/* ── Page header ─────────────────────────────────────────────────── */}
      <div className="h-11 shrink-0 bg-gray-900 border-b border-gray-800 flex items-center px-4 gap-4">
        <span className="text-sm font-bold text-gray-100">Skill Flow Editor</span>

        {selectedId && (
          <span className="text-xs text-gray-500 font-mono">
            {selectedId}
            {isNew      && <span className="text-yellow-400 ml-1">(nova)</span>}
            {isModified && !isNew && <span className="text-yellow-400 ml-1">●</span>}
          </span>
        )}

        <div className="flex-1" />

        {/* Action buttons */}
        <div className="flex items-center gap-2 pr-2">
          <span className="text-xs text-gray-600 hidden lg:block">⌘S para salvar</span>

          {isModified && (
            <button
              onClick={handleDiscard}
              className="px-3 py-1 text-xs font-semibold rounded border border-gray-700 text-gray-400 hover:bg-gray-800 transition-colors"
            >
              Descartar
            </button>
          )}

          {confirmDel ? (
            <>
              <span className="text-xs text-red-400">Remover {selectedId}?</span>
              <button
                onClick={() => void handleDelete()}
                className="px-3 py-1 text-xs font-bold rounded border border-red-700 bg-red-950 text-red-400 hover:bg-red-900 transition-colors"
              >
                Confirmar
              </button>
              <button
                onClick={() => setConfirmDel(false)}
                className="px-3 py-1 text-xs rounded border border-gray-700 text-gray-400 hover:bg-gray-800 transition-colors"
              >
                Cancelar
              </button>
            </>
          ) : (
            selectedId && !isNew && (
              <button
                onClick={() => setConfirmDel(true)}
                className="px-3 py-1 text-xs font-semibold rounded border border-red-800/50 text-red-400 hover:bg-red-950 transition-colors"
              >
                Remover
              </button>
            )
          )}

          <button
            onClick={() => void handleSave()}
            disabled={statusKind === 'saving' || (!isModified && !isNew)}
            className="px-4 py-1 text-xs font-bold rounded border border-violet-600 bg-violet-950 text-violet-300 hover:bg-violet-900 transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
          >
            {statusKind === 'saving' ? 'Salvando…' : '⌘S Salvar'}
          </button>
        </div>
      </div>

      {/* ── Body ──────────────────────────────────────────────────────────── */}
      <div className="flex flex-1 overflow-hidden">

        {/* ── Left sidebar: skill list ───────────────────────────────────── */}
        <div className="w-60 shrink-0 bg-gray-900 border-r border-gray-800 flex flex-col overflow-hidden">
          {/* Search + New */}
          <div className="px-3 py-2.5 border-b border-gray-800 shrink-0">
            <input
              value={search}
              onChange={e => setSearch(e.target.value)}
              placeholder="Buscar skills…"
              className="w-full px-2 py-1.5 text-xs border border-gray-700 rounded bg-gray-800 text-gray-200 focus:outline-none focus:border-violet-600 placeholder-gray-500"
            />
            <button
              onClick={() => setNewPrompt(p => !p)}
              className="w-full mt-2 py-1.5 text-xs font-bold rounded border border-violet-700/60 bg-violet-950 text-violet-400 hover:bg-violet-900 transition-colors"
            >
              + Nova Skill
            </button>
          </div>

          {/* New skill prompt */}
          {newPrompt && (
            <NewSkillPrompt
              onConfirm={handleNewConfirm}
              onCancel={() => setNewPrompt(false)}
            />
          )}

          {/* Skill list */}
          <div className="flex-1 overflow-y-auto">
            {listLoading && filteredSkills.length === 0 && (
              <div className="flex justify-center py-8">
                <Spinner />
              </div>
            )}
            {!listLoading && filteredSkills.length === 0 && (
              <p className="text-center text-xs text-gray-500 py-6">
                {search ? 'Nenhum resultado' : 'Nenhuma skill no registry'}
              </p>
            )}
            {filteredSkills.map(s => (
              <SkillListItem
                key={s.skill_id}
                skill={s as ClassificationAware}
                selected={selectedId === s.skill_id}
                modified={selectedId === s.skill_id && isModified}
                onClick={() => selectSkill(s.skill_id)}
              />
            ))}
          </div>

          {/* Footer: count */}
          <div className="px-3 py-2 border-t border-gray-800 text-xs text-gray-600 shrink-0">
            {skills.length} skill{skills.length !== 1 ? 's' : ''} no registry
          </div>
        </div>

        {/* ── Editor area ───────────────────────────────────────────────── */}
        <div className="flex flex-col flex-1 overflow-hidden">
          {/* Hint bar */}
          <div className="h-7 shrink-0 bg-gray-950 border-b border-gray-800 flex items-center px-4 gap-3 text-xs text-gray-600">
            <span>YAML</span>
            <span>·</span>
            <span>⌘S para salvar</span>
            <span>·</span>
            <span className="hidden md:block">
              Campos: skill_id · name · version · description · classification · tools · flow
            </span>
          </div>

          {/* Monaco */}
          <div className="flex-1 overflow-hidden">
            <Editor
              key={selectedId ?? '__new__'}
              height="100%"
              defaultLanguage="yaml"
              theme="vs-dark"
              value={editorValue}
              onChange={handleEditorChange}
              onMount={editor => { editorRef.current = editor }}
              options={{
                fontSize:             13,
                lineNumbers:          'on',
                minimap:              { enabled: false },
                scrollBeyondLastLine: false,
                wordWrap:             'on',
                tabSize:              2,
                insertSpaces:         true,
                renderWhitespace:     'boundary',
                bracketPairColorization: { enabled: true },
                padding:              { top: 12 },
                fontFamily: "'JetBrains Mono', 'Fira Code', Consolas, monospace",
              }}
            />
          </div>

          <StatusBar kind={statusKind} message={statusMsg} />
        </div>
      </div>
    </div>
  )
}

export default SkillFlowsPage
