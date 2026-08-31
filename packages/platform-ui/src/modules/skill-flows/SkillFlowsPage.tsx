/**
 * SkillFlowsPage.tsx
 * Monaco-based YAML editor for SkillFlow definitions.
 *
 * Layout:
 *   ┌──────────────────────────────────────────────────────┐
 *   │  Page header (keyboard hint + action buttons)        │
 *   ├──────────────┬───────────────────────────────────────┤
 *   │  Skill tree  │  Monaco YAML editor  (vs-dark)        │
 *   │  search      │                                       │
 *   │  ▾ Agents    │  ─────────────────────────────────    │
 *   │    ▾ folder  │  Status bar                           │
 *   │      skill   │                                       │
 *   │  ▾ Workflows │                                       │
 *   └──────────────┴───────────────────────────────────────┘
 *
 * Theme note: sidebar/header/status-bar and Monaco editor all use the light
 * design-system tokens (theme="vs"). Consistent with VS Code / Notepad++ light
 * mode which uses white background for code editing too.
 *
 * Grouping rules:
 *   - classification.type === 'orchestrator'  → Workflows group
 *   - everything else                         → Agents group
 *   - skill YAML field `folder: "path/sub"`   → tree organisation (view-only, not physical)
 *   - max 2 folder levels; search collapses tree to flat list
 */
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import Editor from '@monaco-editor/react'
import * as yaml from 'js-yaml'
import { useTranslation } from 'react-i18next'
import { useAuth } from '@/auth/useAuth'
import { getAccessToken } from '@/auth/token-store'
import Spinner from '@/components/ui/Spinner'
import type { Skill } from '@/types'
import { apiFetch } from '@/api/apiFetch'

// ── Blank template ──────────────────────────────────────────────────────────

const BLANK_TEMPLATE = `# New Skill — fill in the required fields below
# skill_id: nome único e estável (slug), sem versão. Versão é do deploy.
skill_id: skill_novo
name: "New Skill"
description: "Describe what this skill does."

# Optional: visual grouping in the editor sidebar (not a physical path)
# folder: "project/subfolder"

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

// ── Types ─────────────────────────────────────────────────────────────────────

type ClassificationAware = Skill & {
  classification?: { type?: string; domain?: string }
  flow_model?:     'agent' | 'workflow'
  folder?:         string
}

type FolderNode = {
  path:     string
  label:    string
  skills:   ClassificationAware[]
  children: FolderNode[]
}

type RootGroup = {
  key:     'agents' | 'workflows'
  label:   string
  folders: FolderNode[]
  unfiled: ClassificationAware[]
  total:   number
}

// ── Helpers ──────────────────────────────────────────────────────────────────

function skillToYaml(obj: Record<string, unknown>): string {
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

// G-PROBE: o agent-registry gateia as MUTAÇÕES de config (skills/pools/canais) em
// Bearer + ABAC. Este helper não anexava o Authorization, então todo `PUT /v1/skills`
// do editor voltava **401** — ou seja, o editor de skills nunca conseguiu salvar.
// (Pools e canais funcionavam porque usam `api/registry.ts`, que já anexa o Bearer.)
// GETs seguem abertos; o header é ignorado neles.
const operatorHeaders = (tenantId: string) => {
  const token = getAccessToken()
  return {
    'Content-Type': 'application/json',
    'x-tenant-id': tenantId,
    'x-user-id': 'operator',
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
  }
}

/** Turns a validation payload into something a human can act on.
 *  Antes isto fazia `String(body.detail)` — e quando o servidor devolvia um array de
 *  erros Zod, o operador via literalmente "[object Object],[object Object],…", sem a
 *  menor pista de qual campo estava errado. */
function _formatApiError(body: Record<string, unknown>, status: number): string {
  const detail = body["detail"] ?? body["details"] ?? body["error"]
  if (typeof detail === "string") return detail
  if (Array.isArray(detail)) {
    return detail
      .map((e) => {
        const o = e as Record<string, unknown>
        const path = Array.isArray(o["path"]) ? (o["path"] as unknown[]).join(".") : ""
        const msg  = (o["message"] as string) ?? JSON.stringify(o)
        return path ? `${path}: ${msg}` : msg
      })
      .join(" · ")
  }
  if (detail && typeof detail === "object") return JSON.stringify(detail)
  return `HTTP ${status}`
}

async function apiFetchRaw(url: string, init?: RequestInit): Promise<Record<string, unknown>> {
  const res = await apiFetch(url, init)
  if (!res.ok) {
    const body = await res.json().catch(() => ({})) as Record<string, unknown>
    throw new Error(_formatApiError(body, res.status))
  }
  return res.json() as Promise<Record<string, unknown>>
}

// ── Folder tree builder ───────────────────────────────────────────────────────

function buildFolderTree(allSkills: ClassificationAware[]): RootGroup[] {
  const agents    = allSkills.filter(s => s.flow_model !== 'workflow')
  const workflows = allSkills.filter(s => s.flow_model === 'workflow')

  const groups: RootGroup[] = []

  for (const [key, label, list] of [
    ['agents',    'agents',    agents]    as const,
    ['workflows', 'workflows', workflows] as const,
  ]) {
    if (list.length === 0) continue

    const folderMap = new Map<string, ClassificationAware[]>()
    const unfiled: ClassificationAware[] = []

    for (const skill of list) {
      const raw    = skill.folder
      const folder = raw?.trim().replace(/^\/+|\/+$/g, '')
      if (!folder) { unfiled.push(skill); continue }
      if (!folderMap.has(folder)) folderMap.set(folder, [])
      folderMap.get(folder)!.push(skill)
    }

    const rootMap = new Map<string, FolderNode>()
    for (const [path, skills] of folderMap) {
      const parts = path.split('/').slice(0, 2)
      const root  = parts[0]

      if (!rootMap.has(root)) {
        rootMap.set(root, { path: root, label: root, skills: [], children: [] })
      }
      const rootNode = rootMap.get(root)!

      if (parts.length === 1) {
        rootNode.skills.push(...skills)
      } else {
        const subPath = `${root}/${parts[1]}`
        let sub = rootNode.children.find(c => c.path === subPath)
        if (!sub) {
          sub = { path: subPath, label: parts[1], skills: [], children: [] }
          rootNode.children.push(sub)
        }
        sub.skills.push(...skills)
      }
    }

    const folders = Array.from(rootMap.values()).sort((a, b) => a.label.localeCompare(b.label))
    groups.push({ key, label, folders, unfiled, total: list.length })
  }

  return groups
}

function folderTotal(node: FolderNode): number {
  return node.skills.length + node.children.reduce((sum, c) => sum + folderTotal(c), 0)
}

// ── Status bar ───────────────────────────────────────────────────────────────
// Light-theme colors; the bar sits at the bottom of the (dark) Monaco pane.

type StatusKind = 'idle' | 'loading' | 'saving' | 'saved' | 'error' | 'parse_error'

const STATUS_BG: Record<StatusKind, string> = {
  idle:        'bg-white border-t border-border',
  loading:     'bg-white border-t border-border',
  saving:      'bg-primary/10 border-t border-primary/20',
  saved:       'bg-green-50 border-t border-green-200',
  error:       'bg-red-light border-t border-red/30',
  parse_error: 'bg-red-light border-t border-red/30',
}

const STATUS_COLOR: Record<StatusKind, string> = {
  idle:        'text-muted',
  loading:     'text-muted',
  saving:      'text-primary',
  saved:       'text-green-700',
  error:       'text-red-text',
  parse_error: 'text-red-text',
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
    <div className={`relative z-10 h-7 shrink-0 flex items-center px-4 gap-2 text-xs font-mono ${STATUS_BG[kind]} ${STATUS_COLOR[kind]}`}>
      {STATUS_ICON[kind] && <span>{STATUS_ICON[kind]}</span>}
      <span>{message}</span>
    </div>
  )
}

// ── Skill list item ───────────────────────────────────────────────────────────

/** Classification type badge colors — semantic, data-driven, stay as Tailwind */
function typeColor(type: string | undefined): string {
  if (type === 'orchestrator') return 'text-violet-600'
  if (type === 'vertical')     return 'text-sky-600'
  return 'text-amber-600'
}

function SkillListItem({
  skill, selected, modified, indent = 12, onClick,
}: {
  skill:    ClassificationAware
  selected: boolean
  modified: boolean
  indent?:  number
  onClick:  () => void
}) {
  return (
    <div
      onClick={onClick}
      style={{
        paddingLeft:  `${indent}px`,
        background:   selected ? '#EBF2FA' : undefined,
        borderLeft:   `2px solid ${selected ? '#1B4F8A' : 'transparent'}`,
      }}
      className="cursor-pointer border-b border-border pr-3 py-2.5 transition-colors hover:bg-primary/5"
    >
      <div className="flex items-center gap-1.5 mb-0.5">
        {modified && <span className="text-warning text-xs font-bold shrink-0">●</span>}
        <span className="text-xs font-semibold text-secondary font-mono truncate">
          {skill.skill_id}
        </span>
      </div>
      <div className="flex items-center gap-2">
        <span className={`text-xs ${typeColor(skill.classification?.type)}`}>
          {skill.classification?.type ?? '—'}
        </span>
        <span className="text-xs text-muted">v{skill.version}</span>
        {skill.status !== 'active' && (
          <span className="text-xs text-muted">{skill.status}</span>
        )}
      </div>
    </div>
  )
}

// ── Folder node ───────────────────────────────────────────────────────────────

function FolderItem({
  node, depth, selectedId, isModified, expandedFolders, onToggle, onSelect,
}: {
  node:            FolderNode
  depth:           number
  selectedId:      string | null
  isModified:      boolean
  expandedFolders: Set<string>
  onToggle:        (path: string) => void
  onSelect:        (skillId: string) => void
}) {
  const isOpen      = expandedFolders.has(node.path)
  const baseIndent  = 12 + depth * 14
  const childIndent = baseIndent + 14

  return (
    <div>
      <div
        onClick={() => onToggle(node.path)}
        style={{ paddingLeft: `${baseIndent}px` }}
        className="flex items-center gap-1.5 pr-3 py-1.5 cursor-pointer hover:bg-surface-muted border-b border-border/50"
      >
        <span className="text-muted-light text-[10px] w-3 shrink-0">{isOpen ? '▾' : '▸'}</span>
        <span className="text-xs text-muted font-medium truncate">{node.label}</span>
        <span className="ml-auto text-[10px] text-muted-light">{folderTotal(node)}</span>
      </div>

      {isOpen && (
        <>
          {node.children.map(child => (
            <FolderItem
              key={child.path}
              node={child}
              depth={depth + 1}
              selectedId={selectedId}
              isModified={isModified}
              expandedFolders={expandedFolders}
              onToggle={onToggle}
              onSelect={onSelect}
            />
          ))}
          {node.skills.map(s => (
            <SkillListItem
              key={s.skill_id}
              skill={s}
              selected={selectedId === s.skill_id}
              modified={selectedId === s.skill_id && isModified}
              indent={childIndent}
              onClick={() => onSelect(s.skill_id)}
            />
          ))}
        </>
      )}
    </div>
  )
}

// ── Root group section ────────────────────────────────────────────────────────

function RootGroupSection({
  group, selectedId, isModified, expandedFolders, isExpanded,
  onToggleRoot, onToggleFolder, onSelect,
}: {
  group:           RootGroup
  selectedId:      string | null
  isModified:      boolean
  expandedFolders: Set<string>
  isExpanded:      boolean
  onToggleRoot:    (key: string) => void
  onToggleFolder:  (path: string) => void
  onSelect:        (skillId: string) => void
}) {
  const { t } = useTranslation('agentFlow')

  return (
    <div>
      <div
        onClick={() => onToggleRoot(group.key)}
        className="flex items-center gap-1.5 px-3 py-2 cursor-pointer hover:bg-surface-muted border-b border-border sticky top-0 bg-white z-10"
      >
        <span className="text-muted text-[10px] w-3 shrink-0">{isExpanded ? '▾' : '▸'}</span>
        <span className="text-xs font-bold text-dark uppercase tracking-wider">
          {t(`editor.groups.${group.key}`)}
        </span>
        <span className="ml-auto text-[10px] text-muted-light">{group.total}</span>
      </div>

      {isExpanded && (
        <>
          {group.folders.map(folder => (
            <FolderItem
              key={folder.path}
              node={folder}
              depth={0}
              selectedId={selectedId}
              isModified={isModified}
              expandedFolders={expandedFolders}
              onToggle={onToggleFolder}
              onSelect={onSelect}
            />
          ))}
          {group.unfiled.map(s => (
            <SkillListItem
              key={s.skill_id}
              skill={s}
              selected={selectedId === s.skill_id}
              modified={selectedId === s.skill_id && isModified}
              indent={16}
              onClick={() => onSelect(s.skill_id)}
            />
          ))}
        </>
      )}
    </div>
  )
}

// ── SkillFlowsPage ────────────────────────────────────────────────────────────

const SkillFlowsPage: React.FC = () => {
  const { tenantId } = useAuth()
  const { t } = useTranslation('agentFlow')

  // ── Skill list state ───────────────────────────────────────────────────────
  const [skills,      setSkills]      = useState<Skill[]>([])
  const [listLoading, setListLoading] = useState(false)

  const refreshList = useCallback(async () => {
    if (!tenantId) return
    setListLoading(true)
    try {
      const res = await apiFetch('/v1/skills', { headers: operatorHeaders(tenantId) })
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

  // ── Folder tree state ──────────────────────────────────────────────────────
  const [expandedRoots,   setExpandedRoots]   = useState<Set<string>>(new Set(['agents', 'workflows']))
  const [expandedFolders, setExpandedFolders] = useState<Set<string>>(new Set())

  const folderTree = useMemo(
    () => buildFolderTree(skills as ClassificationAware[]),
    [skills],
  )

  function toggleRoot(key: string) {
    setExpandedRoots(prev => {
      const next = new Set(prev)
      if (next.has(key)) next.delete(key); else next.add(key)
      return next
    })
  }

  function toggleFolder(path: string) {
    setExpandedFolders(prev => {
      const next = new Set(prev)
      if (next.has(path)) next.delete(path); else next.add(path)
      return next
    })
  }

  // ── Editor state ───────────────────────────────────────────────────────────
  const [selectedId,  setSelectedId]  = useState<string | null>(null)
  const [editorValue, setEditorValue] = useState(BLANK_TEMPLATE)
  const [savedValue,  setSavedValue]  = useState(BLANK_TEMPLATE)
  const [statusKind,  setStatusKind]  = useState<StatusKind>('idle')
  const [statusMsg,   setStatusMsg]   = useState(() => t('editor.status.idle'))
  const [search,      setSearch]      = useState('')
  const [confirmDel,  setConfirmDel]  = useState(false)
  // isNew: skill em autoria, ainda não salvo. Habilita o Save mesmo sem isModified
  // (o template em branco tem savedValue == editorValue → Save pareceria travado).
  const [isNew,       setIsNew]       = useState(true)

  const isModified = editorValue !== savedValue
  const canSave    = statusKind !== 'saving' && (isModified || isNew)
  const editorRef  = useRef<unknown>(null)

  // ── Load skill ─────────────────────────────────────────────────────────────
  const loadSkill = useCallback(async (skillId: string) => {
    setStatusKind('loading')
    setStatusMsg(t('editor.status.loading', { id: skillId }))
    setConfirmDel(false)
    try {
      const data = await apiFetchRaw(`/v1/skills/${encodeURIComponent(skillId)}`, {
        headers: operatorHeaders(tenantId),
      })
      // O editor edita a DEFINIÇÃO (`flow`). Não há mais rascunho.
      //
      // Dois saneamentos no round-trip GET → YAML → PUT, que estava quebrado por
      // construção (nunca notado porque o save voltava 401 e nem chegava ao servidor):
      //
      //  1. Campos gerenciados pelo SERVIDOR não são editáveis e não devem aparecer no
      //     YAML — só poluem e voltam no PUT (created_at, deploy_status, flow_model…).
      //  2. Campos opcionais vêm como `null` do banco. O `SkillSchema` aceita
      //     *ausente* (optional) mas NÃO `null` — então devolver `instruction: null`,
      //     `interface: null`, etc. fazia o PUT falhar com um erro por campo nulo.
      //     Omitir os nulos é equivalente (campo não setado) e mantém o YAML limpo.
      const {
        flow_draft, unpublished_draft, flow,
        id, tenant_id, status, created_by, created_at, updated_at,
        deploy_status, published_at, flow_model,
        ...restData
      } = data as Record<string, unknown>

      const working = Object.fromEntries(
        Object.entries({ ...restData, flow: (flow ?? flow_draft) ?? undefined })
          .filter(([, v]) => v !== null && v !== undefined),
      )
      const yamlText = skillToYaml(working)
      setEditorValue(yamlText)
      setSavedValue(yamlText)
      setStatusKind('idle')
      setStatusMsg(t('editor.status.loaded', { id: skillId }))
    } catch (e: unknown) {
      setStatusKind('error')
      setStatusMsg(e instanceof Error ? e.message : t('editor.status.loadFailed'))
    }
  }, [tenantId, t])

  function selectSkill(skillId: string) {
    if ((isModified || isNew) && editorValue !== BLANK_TEMPLATE && !confirm(t('editor.discardConfirm'))) return
    setIsNew(false)
    setSelectedId(skillId)
    void loadSkill(skillId)
  }

  // ── Novo skill ───────────────────────────────────────────────────────────
  // Começa a autoria de um skill novo: limpa a seleção, carrega o template e
  // marca isNew (habilita o Save). Descobrível (botão), não-implícito.
  function handleNew() {
    if ((isModified || isNew) && editorValue !== BLANK_TEMPLATE && !confirm(t('editor.discardConfirm'))) return
    setSelectedId(null)
    setEditorValue(BLANK_TEMPLATE)
    setSavedValue(BLANK_TEMPLATE)
    setConfirmDel(false)
    setIsNew(true)
    setStatusKind('idle')
    setStatusMsg(t('editor.status.newSkillStatus'))
  }

  // ── Save ───────────────────────────────────────────────────────────────────
  async function handleSave() {
    const parseResult = yamlToJson(editorValue)
    if (!parseResult.ok) {
      setStatusKind('parse_error')
      setStatusMsg(t('editor.status.yamlError', { error: parseResult.error }))
      return
    }
    const payload  = parseResult.data
    const skillId  = (payload.skill_id as string | undefined) || selectedId
    if (!skillId) {
      setStatusKind('error')
      setStatusMsg(t('editor.status.requiredId'))
      return
    }

    setStatusKind('saving')
    setStatusMsg(t('editor.status.saving', { id: skillId }))
    try {
      await apiFetchRaw(`/v1/skills/${encodeURIComponent(skillId)}`, {
        method:  'PUT',
        headers: operatorHeaders(tenantId),
        body:    JSON.stringify(payload),
      })
      setSavedValue(editorValue)
      setSelectedId(skillId)
      setIsNew(false)               // skill agora existe → sai do modo "novo"
      setStatusKind('saved')
      setStatusMsg(t('editor.status.saved', { id: skillId }))
      void refreshList()
    } catch (e: unknown) {
      setStatusKind('error')
      setStatusMsg(e instanceof Error ? e.message : t('editor.status.saveFailed'))
    }
  }

  // ── Delete ─────────────────────────────────────────────────────────────────
  async function handleDelete() {
    if (!selectedId) return
    try {
      const res = await apiFetch(`/v1/skills/${encodeURIComponent(selectedId)}`, {
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
      setConfirmDel(false)
      setIsNew(true)                // volta ao template em branco = novo skill
      setStatusKind('idle')
      setStatusMsg(t('editor.status.deleted'))
      void refreshList()
    } catch (e: unknown) {
      setStatusKind('error')
      setStatusMsg(e instanceof Error ? e.message : t('editor.status.deleteFailed'))
    }
  }

  // ── Discard ────────────────────────────────────────────────────────────────
  function handleDiscard() {
    if (!confirm(t('editor.discardAllConfirm'))) return
    if (selectedId) void loadSkill(selectedId)
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
      setStatusMsg(selectedId
        ? t('editor.status.editing', { id: selectedId })
        : t('editor.status.newSkillStatus'))
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

  // ── Flat filtered list (search mode) ──────────────────────────────────────
  const filteredSkills = skills.filter(s =>
    !search ||
    s.skill_id.toLowerCase().includes(search.toLowerCase()) ||
    s.name.toLowerCase().includes(search.toLowerCase())
  )

  // ── Render ─────────────────────────────────────────────────────────────────
  return (
    <div className="flex flex-col h-full bg-surface-muted text-dark">

      {/* ── Page header ─────────────────────────────────────────────────── */}
      <div className="h-11 shrink-0 bg-white border-b border-border flex items-center px-4 gap-4">

        <button
          onClick={handleNew}
          className="px-3 py-1 text-xs font-semibold rounded border border-primary/40 text-primary hover:bg-primary/5 transition-colors"
        >
          + {t('editor.new')}
        </button>

        {selectedId ? (
          <span className="text-xs text-muted font-mono flex items-center gap-2">
            <span>
              {selectedId}
              {isModified && <span className="text-warning ml-1">●</span>}
            </span>
            {/* Badge "rascunho não publicado" REMOVIDO (2026-07-13).
                Não há mais rascunho — o editor grava a definição. E "não implantado"
                é uma pergunta POR POOL: o mesmo skill pode estar deployado em N pools,
                cada um com um snapshot diferente. O editor não tem como responder isso
                sem mentir. Quem responde é a tela de Deploy, comparando o `updated_at`
                da definição com o `set_at` do slot de CADA pool. */}
          </span>
        ) : isNew && (
          <span className="text-xs text-primary font-mono">{t('editor.newSkill')}</span>
        )}

        <div className="flex-1" />

        <div className="flex items-center gap-2 pr-2">
          <span className="text-xs text-muted-light hidden lg:block">{t('editor.saveShortcut')}</span>

          {isModified && (
            <button
              onClick={handleDiscard}
              className="px-3 py-1 text-xs font-semibold rounded border border-border text-muted hover:bg-surface-muted transition-colors"
            >
              {t('editor.discard')}
            </button>
          )}

          {confirmDel ? (
            <>
              <span className="text-xs text-red">{t('editor.confirmDeletePrompt', { id: selectedId })}</span>
              <button
                onClick={() => void handleDelete()}
                className="px-3 py-1 text-xs font-bold rounded border border-red/30 bg-red-light text-red-text hover:opacity-90 transition-opacity"
              >
                {t('editor.confirmDelete')}
              </button>
              <button
                onClick={() => setConfirmDel(false)}
                className="px-3 py-1 text-xs rounded border border-border text-muted hover:bg-surface-muted transition-colors"
              >
                {t('editor.cancel')}
              </button>
            </>
          ) : (
            selectedId && (
              <button
                onClick={() => setConfirmDel(true)}
                className="px-3 py-1 text-xs font-semibold rounded border border-red/30 text-red hover:bg-red-light transition-colors"
              >
                {t('editor.delete')}
              </button>
            )
          )}

          {!canSave && selectedId && !isModified && (
            <span className="text-2xs text-muted-light hidden lg:block">{t('editor.saveHintView')}</span>
          )}
          <button
            onClick={() => void handleSave()}
            disabled={!canSave}
            title={!canSave ? t('editor.saveHintView') : undefined}
            className="px-4 py-1 text-xs font-bold rounded bg-primary text-white hover:bg-primary/90 transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
          >
            {statusKind === 'saving' ? t('editor.saving') : t('editor.save')}
          </button>
        </div>
      </div>

      {/* ── Body ──────────────────────────────────────────────────────────── */}
      <div className="flex flex-1 overflow-hidden">

        {/* ── Left sidebar: skill tree ───────────────────────────────────── */}
        <div className="w-64 shrink-0 bg-white border-r border-border flex flex-col overflow-hidden">

          {/* Search */}
          <div className="px-3 py-2.5 border-b border-border shrink-0">
            <input
              value={search}
              onChange={e => setSearch(e.target.value)}
              placeholder={t('editor.search')}
              className="w-full px-2.5 py-1.5 text-xs border border-border-strong rounded-md bg-white text-dark focus:outline-none focus:border-primary placeholder:text-muted"
            />
          </div>

          {/* Tree or flat list */}
          <div className="flex-1 overflow-y-auto">
            {listLoading && skills.length === 0 && (
              <div className="flex justify-center py-8">
                <Spinner />
              </div>
            )}

            {/* ── Search mode: flat list ─────────────────────────────────── */}
            {search && (
              <>
                {filteredSkills.length === 0 && (
                  <p className="text-center text-xs text-muted-light py-6">{t('editor.noResults')}</p>
                )}
                {filteredSkills.map(s => (
                  <SkillListItem
                    key={s.skill_id}
                    skill={s as ClassificationAware}
                    selected={selectedId === s.skill_id}
                    modified={selectedId === s.skill_id && isModified}
                    indent={12}
                    onClick={() => selectSkill(s.skill_id)}
                  />
                ))}
              </>
            )}

            {/* ── Browse mode: folder tree ───────────────────────────────── */}
            {!search && (
              <>
                {!listLoading && folderTree.length === 0 && (
                  <p className="text-center text-xs text-muted-light py-6">
                    {t('editor.noRegistry')}
                  </p>
                )}
                {folderTree.map(group => (
                  <RootGroupSection
                    key={group.key}
                    group={group}
                    selectedId={selectedId}
                    isModified={isModified}
                    expandedFolders={expandedFolders}
                    isExpanded={expandedRoots.has(group.key)}
                    onToggleRoot={toggleRoot}
                    onToggleFolder={toggleFolder}
                    onSelect={selectSkill}
                  />
                ))}
              </>
            )}
          </div>

          {/* Footer: count */}
          <div className="px-3 py-2 border-t border-border text-xs text-muted-light shrink-0">
            {t('editor.skillCount', { count: skills.length })}
          </div>
        </div>

        {/* ── Editor area ───────────────────────────────────────────────── */}
        <div className="relative flex flex-col flex-1 min-h-0 overflow-hidden">

          {/* Hint bar — explicitly above Monaco via z-index (Monaco uses absolute layers) */}
          <div className="relative z-10 h-7 shrink-0 bg-white border-b border-border flex items-center px-4 gap-3 text-xs text-muted">
            <span>YAML</span>
            <span className="text-muted-light">·</span>
            <span>{t('editor.saveShortcut')}</span>
            <span className="text-muted-light">·</span>
            <span className="hidden md:block">{t('editor.hintFields')}</span>
          </div>

          {/* Monaco — light theme (vs), consistent with app light mode */}
          <div className="relative flex-1 min-h-0 overflow-hidden bg-white">
            <Editor
              key={selectedId ?? '__new__'}
              height="100%"
              defaultLanguage="yaml"
              theme="vs"
              value={editorValue}
              onChange={handleEditorChange}
              onMount={editor => { editorRef.current = editor }}
              options={{
                fontSize:                13,
                lineNumbers:             'on',
                minimap:                 { enabled: false },
                scrollBeyondLastLine:    false,
                wordWrap:                'on',
                tabSize:                 2,
                insertSpaces:            true,
                renderWhitespace:        'boundary',
                bracketPairColorization: { enabled: true },
                padding:                 { top: 12 },
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
