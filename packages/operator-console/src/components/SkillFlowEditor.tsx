/**
 * SkillFlowEditor.tsx
 * Monaco-based YAML editor for SkillFlow definitions.
 *
 * Layout:
 *   ┌─────────────────────────────────────────────────────────────────┐
 *   │ ← Back   Skill Flow Editor                           [actions]  │
 *   ├────────────────┬────────────────────────────────────────────────┤
 *   │  Skill list    │                                                │
 *   │  ─────────     │  Monaco YAML editor                           │
 *   │  search        │                                                │
 *   │  + New         │                                                │
 *   │  rows…         │                                                │
 *   └────────────────┴────────────────────────────────────────────────┘
 *
 * Skills are stored as JSON in the Agent Registry API but displayed here
 * as YAML for readability. On save, the YAML is parsed back to JSON and
 * PUT to /v1/skills/:id.
 */
import { useEffect, useRef, useState, useCallback } from 'react'
import Editor from '@monaco-editor/react'
import * as yaml from 'js-yaml'
import { useSkills, fetchSkill, upsertSkill, deleteSkill } from '../api/registry-hooks'
import type { RegistrySkill } from '../types'

// ── Palette ────────────────────────────────────────────────────────────────
const C = {
  bg:         '#0d1117',
  surface:    '#0f1923',
  border:     '#1e293b',
  text:       '#e2e8f0',
  muted:      '#64748b',
  accent:     '#a78bfa',     // violet — distinct from other panels
  accentDark: '#2e1065',
  green:      '#22c55e',
  red:        '#ef4444',
  yellow:     '#fbbf24',
  cyan:       '#22d3ee',
  orange:     '#f97316',
} as const

// ── Blank skill template ───────────────────────────────────────────────────

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

# flow: required only for orchestrator skills
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

// ── Helpers ────────────────────────────────────────────────────────────────

function skillToYaml(obj: Record<string, unknown>): string {
  // Strip API-added fields before displaying
  const { tenant_id, created_at, updated_at, created_by, status, ...rest } = obj as Record<string, unknown>
  void tenant_id; void created_at; void updated_at; void created_by; void status
  try {
    return yaml.dump(rest, { lineWidth: 100, indent: 2, noRefs: true })
  } catch {
    return JSON.stringify(rest, null, 2)
  }
}

function yamlToJson(text: string): { ok: true; data: Record<string, unknown> } | { ok: false; error: string } {
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

// ── Skill list sidebar ─────────────────────────────────────────────────────

function SkillListItem({ skill, selected, modified, onClick }: {
  skill: RegistrySkill
  selected: boolean
  modified: boolean
  onClick: () => void
}) {
  const typeColor = skill.classification.type === 'orchestrator' ? C.accent
                  : skill.classification.type === 'vertical'     ? C.cyan : C.yellow
  return (
    <div onClick={onClick} style={{
      padding: '9px 12px', cursor: 'pointer',
      background: selected ? '#1e293b' : 'transparent',
      borderBottom: `1px solid ${C.border}`,
      borderLeft: `3px solid ${selected ? C.accent : 'transparent'}`,
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 5, marginBottom: 2 }}>
        {modified && <span style={{ color: C.yellow, fontSize: 10, fontWeight: 800 }}>●</span>}
        <span style={{ fontSize: 11, fontWeight: 600, color: C.text, fontFamily: 'monospace',
          overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
          {skill.skill_id}
        </span>
      </div>
      <div style={{ display: 'flex', gap: 4 }}>
        <span style={{ fontSize: 10, color: typeColor }}>{skill.classification.type}</span>
        <span style={{ fontSize: 10, color: C.muted }}>v{skill.version}</span>
        {skill.status !== 'active' && (
          <span style={{ fontSize: 10, color: C.muted }}>{skill.status}</span>
        )}
      </div>
    </div>
  )
}

// ── Status bar ─────────────────────────────────────────────────────────────

type StatusKind = 'idle' | 'loading' | 'saving' | 'saved' | 'error' | 'parse_error'

function StatusBar({ kind, message }: { kind: StatusKind; message: string }) {
  const bg = kind === 'error' || kind === 'parse_error' ? '#450a0a'
           : kind === 'saved'   ? '#052e16'
           : kind === 'saving'  ? '#1e1b4b'
           : '#0f1923'
  const color = kind === 'error' || kind === 'parse_error' ? C.red
              : kind === 'saved'   ? C.green
              : kind === 'saving'  ? C.accent
              : C.muted
  return (
    <div style={{
      height: 28, flexShrink: 0,
      background: bg, borderTop: `1px solid ${C.border}`,
      display: 'flex', alignItems: 'center', padding: '0 16px',
      fontSize: 11, color, fontFamily: 'monospace', gap: 8,
    }}>
      {kind === 'loading' && <span>⏳</span>}
      {kind === 'saving'  && <span>💾</span>}
      {kind === 'saved'   && <span>✓</span>}
      {(kind === 'error' || kind === 'parse_error') && <span>✗</span>}
      {message}
    </div>
  )
}

// ── Action button ──────────────────────────────────────────────────────────

function ActionBtn({ label, onClick, color = C.muted, bg = 'transparent', disabled }: {
  label: string; onClick: () => void; color?: string; bg?: string; disabled?: boolean
}) {
  return (
    <button onClick={onClick} disabled={disabled} style={{
      padding: '4px 12px', borderRadius: 4, fontSize: 12, fontWeight: 600,
      border: `1px solid ${color}55`, background: bg, color,
      cursor: disabled ? 'not-allowed' : 'pointer', opacity: disabled ? 0.5 : 1,
    }}>
      {label}
    </button>
  )
}

// ── New Skill form (inline, simple) ────────────────────────────────────────

function NewSkillPrompt({ onConfirm, onCancel }: {
  onConfirm: (skillId: string) => void
  onCancel:  () => void
}) {
  const [val, setVal] = useState('skill_novo_v1')
  return (
    <div style={{
      padding: '12px 14px', borderBottom: `1px solid ${C.border}`,
      background: C.accentDark,
    }}>
      <div style={{ fontSize: 11, color: C.accent, fontWeight: 700, marginBottom: 6 }}>
        New Skill ID
      </div>
      <input value={val} onChange={e => setVal(e.target.value)}
        style={{
          width: '100%', padding: '5px 8px', borderRadius: 4, fontSize: 11,
          border: `1px solid ${C.accent}55`, background: '#1e293b',
          color: C.text, outline: 'none', boxSizing: 'border-box', fontFamily: 'monospace',
        }}
        placeholder="skill_name_v1"
        onKeyDown={e => { if (e.key === 'Enter') onConfirm(val) }}
      />
      <div style={{ display: 'flex', gap: 6, marginTop: 8 }}>
        <button onClick={() => onConfirm(val)} style={{
          padding: '3px 10px', borderRadius: 3, fontSize: 11, fontWeight: 700,
          background: C.accentDark, border: `1px solid ${C.accent}`, color: C.accent, cursor: 'pointer',
        }}>
          Create
        </button>
        <button onClick={onCancel} style={{
          padding: '3px 10px', borderRadius: 3, fontSize: 11,
          background: 'transparent', border: `1px solid ${C.border}`, color: C.muted, cursor: 'pointer',
        }}>
          Cancel
        </button>
      </div>
    </div>
  )
}

// ── Main component ─────────────────────────────────────────────────────────

interface Props {
  tenantId: string
  onBack:   () => void
}

export function SkillFlowEditor({ tenantId, onBack }: Props) {
  const { skills, loading: listLoading, refresh: refreshList } = useSkills(tenantId)

  const [selectedSkillId, setSelectedSkillId] = useState<string | null>(null)
  const [editorValue,     setEditorValue]     = useState<string>(BLANK_TEMPLATE)
  const [savedValue,      setSavedValue]      = useState<string>(BLANK_TEMPLATE)  // last saved state
  const [statusKind,      setStatusKind]      = useState<StatusKind>('idle')
  const [statusMsg,       setStatusMsg]       = useState('No skill selected — create a new one or pick from the list')
  const [isNew,           setIsNew]           = useState(false)
  const [newSkillPrompt,  setNewSkillPrompt]  = useState(false)
  const [search,          setSearch]          = useState('')
  const [confirmDelete,   setConfirmDelete]   = useState(false)

  const isModified = editorValue !== savedValue
  const editorRef  = useRef<unknown>(null)

  // ── Load skill when selected ───────────────────────────────────────────

  const loadSkill = useCallback(async (skillId: string) => {
    setStatusKind('loading')
    setStatusMsg(`Loading ${skillId}…`)
    setConfirmDelete(false)
    try {
      const data = await fetchSkill(tenantId, skillId)
      const yamlText = skillToYaml(data)
      setEditorValue(yamlText)
      setSavedValue(yamlText)
      setIsNew(false)
      setStatusKind('idle')
      setStatusMsg(`Loaded ${skillId}`)
    } catch (e: unknown) {
      setStatusKind('error')
      setStatusMsg(e instanceof Error ? e.message : 'Failed to load skill')
    }
  }, [tenantId])

  function selectSkill(skillId: string) {
    if (isModified && !confirm('Discard unsaved changes?')) return
    setSelectedSkillId(skillId)
    loadSkill(skillId)
  }

  // ── New skill ──────────────────────────────────────────────────────────

  function handleNewSkillConfirm(skillId: string) {
    if (isModified && !confirm('Discard unsaved changes?')) return
    const template = BLANK_TEMPLATE.replace('skill_novo_v1', skillId)
    setSelectedSkillId(skillId)
    setEditorValue(template)
    setSavedValue('')        // so isModified = true immediately
    setIsNew(true)
    setNewSkillPrompt(false)
    setStatusKind('idle')
    setStatusMsg(`New skill — ${skillId} (unsaved)`)
  }

  // ── Save ───────────────────────────────────────────────────────────────

  async function handleSave() {
    const parseResult = yamlToJson(editorValue)
    if (!parseResult.ok) {
      setStatusKind('parse_error')
      setStatusMsg(`YAML error: ${parseResult.error}`)
      return
    }
    const payload = parseResult.data
    const skillId = (payload.skill_id as string) || selectedSkillId
    if (!skillId) {
      setStatusKind('error')
      setStatusMsg('skill_id is required in the YAML.')
      return
    }

    setStatusKind('saving')
    setStatusMsg(`Saving ${skillId}…`)
    try {
      await upsertSkill(tenantId, skillId, payload)
      setSavedValue(editorValue)
      setSelectedSkillId(skillId)
      setIsNew(false)
      setStatusKind('saved')
      setStatusMsg(`Saved ${skillId}`)
      refreshList()
    } catch (e: unknown) {
      setStatusKind('error')
      setStatusMsg(e instanceof Error ? e.message : 'Save failed')
    }
  }

  // ── Delete ─────────────────────────────────────────────────────────────

  async function handleDelete() {
    if (!selectedSkillId) return
    try {
      await deleteSkill(tenantId, selectedSkillId)
      setSelectedSkillId(null)
      setEditorValue(BLANK_TEMPLATE)
      setSavedValue(BLANK_TEMPLATE)
      setIsNew(false)
      setConfirmDelete(false)
      setStatusKind('idle')
      setStatusMsg('Skill deleted')
      refreshList()
    } catch (e: unknown) {
      setStatusKind('error')
      setStatusMsg(e instanceof Error ? e.message : 'Delete failed')
    }
  }

  // ── Discard ────────────────────────────────────────────────────────────

  function handleDiscard() {
    if (!confirm('Discard all unsaved changes?')) return
    if (isNew) {
      setSelectedSkillId(null)
      setEditorValue(BLANK_TEMPLATE)
      setSavedValue(BLANK_TEMPLATE)
      setIsNew(false)
      setStatusKind('idle')
      setStatusMsg('No skill selected')
    } else if (selectedSkillId) {
      loadSkill(selectedSkillId)
    }
  }

  // ── Live parse validation on editor change ─────────────────────────────

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
      setStatusMsg(selectedSkillId ? `Editing ${selectedSkillId}` : 'New skill')
    }
  }

  // ── Keyboard shortcuts ─────────────────────────────────────────────────

  useEffect(() => {
    function onKeyDown(e: KeyboardEvent) {
      if ((e.ctrlKey || e.metaKey) && e.key === 's') {
        e.preventDefault()
        handleSave()
      }
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  })

  // ── Filtered skill list ─────────────────────────────────────────────────

  const filteredSkills = skills.filter(s =>
    !search || s.skill_id.toLowerCase().includes(search.toLowerCase()) ||
    s.name.toLowerCase().includes(search.toLowerCase())
  )

  // ── Render ─────────────────────────────────────────────────────────────

  return (
    <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden', background: C.bg }}>

      {/* ── Header bar ──────────────────────────────────────────────────── */}
      <div style={{
        height: 44, flexShrink: 0, borderBottom: `1px solid ${C.border}`,
        display: 'flex', alignItems: 'center', paddingLeft: 12, gap: 12,
        background: C.surface,
      }}>
        <button onClick={onBack} style={{
          background: 'none', border: 'none', color: C.muted,
          cursor: 'pointer', fontSize: 18, padding: '0 4px',
        }} title="Back">←</button>

        <span style={{ fontSize: 14, fontWeight: 700, color: C.text }}>Skill Flow Editor</span>

        {selectedSkillId && (
          <span style={{ fontSize: 11, color: C.muted, fontFamily: 'monospace' }}>
            {selectedSkillId}
            {isNew && <span style={{ color: C.yellow }}> (new)</span>}
            {isModified && !isNew && <span style={{ color: C.yellow }}> ●</span>}
          </span>
        )}

        <div style={{ flex: 1 }} />

        <div style={{ display: 'flex', gap: 6, paddingRight: 14, alignItems: 'center' }}>
          {isModified && (
            <ActionBtn label="Discard" onClick={handleDiscard} color={C.muted} />
          )}
          {confirmDelete
            ? <>
                <span style={{ fontSize: 11, color: C.red }}>Delete {selectedSkillId}?</span>
                <ActionBtn label="Confirm" onClick={handleDelete} color={C.red} bg="#450a0a" />
                <ActionBtn label="Cancel"  onClick={() => setConfirmDelete(false)} />
              </>
            : selectedSkillId && !isNew && (
                <ActionBtn label="Delete" onClick={() => setConfirmDelete(true)} color={C.red} />
              )
          }
          <ActionBtn
            label={statusKind === 'saving' ? 'Saving…' : '⌘S Save'}
            onClick={handleSave}
            color={C.accent}
            bg={C.accentDark}
            disabled={statusKind === 'saving' || (!isModified && !isNew)}
          />
        </div>
      </div>

      {/* ── Body ────────────────────────────────────────────────────────── */}
      <div style={{ flex: 1, display: 'flex', overflow: 'hidden' }}>

        {/* ── Left sidebar: skill list ─────────────────────────────────── */}
        <div style={{
          width: 240, flexShrink: 0, borderRight: `1px solid ${C.border}`,
          display: 'flex', flexDirection: 'column', overflow: 'hidden',
          background: C.surface,
        }}>
          {/* Search + New */}
          <div style={{ padding: '8px 10px', borderBottom: `1px solid ${C.border}`, flexShrink: 0 }}>
            <input
              value={search}
              onChange={e => setSearch(e.target.value)}
              style={{
                width: '100%', padding: '5px 8px', borderRadius: 4, fontSize: 11,
                border: `1px solid ${C.border}`, background: '#1e293b',
                color: C.text, outline: 'none', boxSizing: 'border-box',
              }}
              placeholder="Search skills…"
            />
            <button onClick={() => setNewSkillPrompt(p => !p)} style={{
              width: '100%', marginTop: 6, padding: '5px 0', borderRadius: 4,
              border: `1px solid ${C.accent}55`, background: C.accentDark,
              color: C.accent, fontSize: 11, fontWeight: 700, cursor: 'pointer',
            }}>
              + New Skill
            </button>
          </div>

          {/* New skill prompt */}
          {newSkillPrompt && (
            <NewSkillPrompt
              onConfirm={handleNewSkillConfirm}
              onCancel={() => setNewSkillPrompt(false)}
            />
          )}

          {/* List */}
          <div style={{ flex: 1, overflowY: 'auto' }}>
            {listLoading && filteredSkills.length === 0 && (
              <div style={{ padding: 16, fontSize: 11, color: C.muted, textAlign: 'center' }}>
                Loading…
              </div>
            )}
            {!listLoading && filteredSkills.length === 0 && (
              <div style={{ padding: 16, fontSize: 11, color: C.muted, textAlign: 'center' }}>
                {search ? 'No matches' : 'No skills in registry'}
              </div>
            )}
            {filteredSkills.map(s => (
              <SkillListItem
                key={s.skill_id}
                skill={s}
                selected={selectedSkillId === s.skill_id}
                modified={selectedSkillId === s.skill_id && isModified}
                onClick={() => selectSkill(s.skill_id)}
              />
            ))}
          </div>

          {/* Footer: skill count */}
          <div style={{
            padding: '6px 12px', borderTop: `1px solid ${C.border}`,
            fontSize: 10, color: C.muted, flexShrink: 0,
          }}>
            {skills.length} skill{skills.length !== 1 ? 's' : ''} in registry
          </div>
        </div>

        {/* ── Editor area ──────────────────────────────────────────────── */}
        <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>

          {/* Editor hint bar */}
          <div style={{
            height: 28, flexShrink: 0,
            background: '#0d1117', borderBottom: `1px solid ${C.border}`,
            display: 'flex', alignItems: 'center', padding: '0 14px', gap: 12,
            fontSize: 10, color: C.muted,
          }}>
            <span>YAML</span>
            <span>·</span>
            <span style={{ color: C.muted + 'cc' }}>⌘S to save</span>
            <span>·</span>
            <span style={{ color: C.muted + 'cc' }}>
              Fields: skill_id · name · version · description · classification · tools · flow
            </span>
          </div>

          <div style={{ flex: 1, overflow: 'hidden' }}>
            <Editor
              key={selectedSkillId ?? '__new__'}  // remount when skill changes
              height="100%"
              defaultLanguage="yaml"
              theme="vs-dark"
              value={editorValue}
              onChange={handleEditorChange}
              onMount={(editor) => { editorRef.current = editor }}
              options={{
                fontSize:         13,
                lineNumbers:      'on',
                minimap:          { enabled: false },
                scrollBeyondLastLine: false,
                wordWrap:         'on',
                tabSize:          2,
                insertSpaces:     true,
                renderWhitespace: 'boundary',
                bracketPairColorization: { enabled: true },
                padding:          { top: 12 },
                fontFamily:       "'JetBrains Mono', 'Fira Code', Consolas, monospace",
              }}
            />
          </div>

          <StatusBar kind={statusKind} message={statusMsg} />
        </div>
      </div>
    </div>
  )
}
