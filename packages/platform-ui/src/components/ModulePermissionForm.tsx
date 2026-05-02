/**
 * ModulePermissionForm.tsx
 *
 * Formulário dinâmico de permissões ABAC por módulo.
 * Renderiza campos conforme o permission_schema registrado em auth.module_registry
 * (obtido via GET /auth/modules).
 *
 * Cada campo vira um bloco com:
 *  - Select de acesso (none / read_only / write_only / read_write) filtrado pelo domain
 *  - Input de escopo (pool IDs ou campaign IDs) quando scopable=true e access != none
 *
 * Props:
 *   modules      — lista de módulos retornada por GET /auth/modules
 *   value        — ModuleConfig atual (controlado)
 *   onChange     — chamado com a nova ModuleConfig quando o usuário altera algo
 *   readOnly     — desabilita todos os inputs
 *   className    — classe CSS adicional no wrapper
 */

import React, { useState } from 'react'
import type { ModuleConfig, ModuleFieldConfig, PermissionAccess } from '@/types'

// ── Types para o schema do módulo ─────────────────────────────────────────────

export interface ModuleFieldSchema {
  label: string
  domain: PermissionAccess[]
  scopable: boolean
  scope_type?: 'pool' | 'campaign'
  default: PermissionAccess
}

export interface ModuleSchema {
  module_id: string
  label: string
  icon: string
  nav_path: string
  active: boolean
  permission_schema: Record<string, ModuleFieldSchema>
}

// ── Labels de acesso para o usuário final ─────────────────────────────────────

const ACCESS_LABELS: Record<PermissionAccess, string> = {
  none:       'Sem acesso',
  read_only:  'Somente visualizar',
  write_only: 'Somente escrever',
  read_write: 'Visualizar e editar',
}

// ── ScopeInput ────────────────────────────────────────────────────────────────

function ScopeInput({
  scopeType,
  value,
  onChange,
  disabled,
}: {
  scopeType: 'pool' | 'campaign'
  value: string[]
  onChange: (s: string[]) => void
  disabled: boolean
}) {
  const [draft, setDraft] = useState(value.join(', '))

  const handleBlur = () => {
    const parsed = draft
      .split(',')
      .map(s => s.trim())
      .filter(Boolean)
      .map(s => (s.startsWith(`${scopeType}:`) ? s : `${scopeType}:${s}`))
    setDraft(parsed.join(', '))
    onChange(parsed)
  }

  return (
    <div className="mt-1">
      <label className="text-xs text-gray-500 block mb-0.5">
        Escopo por {scopeType === 'pool' ? 'pool' : 'campanha'}{' '}
        <span className="text-gray-400">(vazio = acesso global)</span>
      </label>
      <input
        type="text"
        disabled={disabled}
        className="w-full text-xs border border-gray-300 rounded px-2 py-1 disabled:bg-gray-50 disabled:text-gray-400"
        placeholder={`Ex: ${scopeType}:retencao_humano, ${scopeType}:sac`}
        value={draft}
        onChange={e => setDraft(e.target.value)}
        onBlur={handleBlur}
      />
      {value.length > 0 && (
        <div className="flex flex-wrap gap-1 mt-1">
          {value.map(v => (
            <span key={v} className="inline-flex items-center gap-0.5 bg-blue-50 text-blue-700 text-xs px-2 py-0.5 rounded">
              {v}
              {!disabled && (
                <button
                  type="button"
                  className="hover:text-blue-900 ml-0.5"
                  onClick={() => {
                    const next = value.filter(x => x !== v)
                    setDraft(next.join(', '))
                    onChange(next)
                  }}
                >✕</button>
              )}
            </span>
          ))}
        </div>
      )}
    </div>
  )
}

// ── FieldRow ──────────────────────────────────────────────────────────────────

function FieldRow({
  fieldKey,
  schema,
  fieldConfig,
  onChange,
  readOnly,
}: {
  fieldKey: string
  schema: ModuleFieldSchema
  fieldConfig: ModuleFieldConfig
  onChange: (next: ModuleFieldConfig) => void
  readOnly: boolean
}) {
  const showScope = schema.scopable && fieldConfig.access !== 'none'

  return (
    <div className="py-3 border-b last:border-0">
      <div className="flex items-start gap-3">
        <div className="flex-1 min-w-0">
          <div className="text-sm font-medium text-gray-800">{schema.label}</div>
          <div className="text-xs text-gray-500 mt-0.5 font-mono">{fieldKey}</div>
        </div>
        <div className="w-52 shrink-0">
          <select
            disabled={readOnly}
            className="w-full text-sm border border-gray-300 rounded px-2 py-1.5 disabled:bg-gray-50"
            value={fieldConfig.access}
            onChange={e => onChange({ ...fieldConfig, access: e.target.value as PermissionAccess })}
          >
            {schema.domain.map(d => (
              <option key={d} value={d}>{ACCESS_LABELS[d] ?? d}</option>
            ))}
          </select>
        </div>
      </div>
      {showScope && schema.scope_type && (
        <div className="mt-2 pl-1">
          <ScopeInput
            scopeType={schema.scope_type}
            value={fieldConfig.scope}
            onChange={scope => onChange({ ...fieldConfig, scope })}
            disabled={readOnly}
          />
        </div>
      )}
    </div>
  )
}

// ── ModuleSection ─────────────────────────────────────────────────────────────

function ModuleSection({
  mod,
  moduleValue,
  onChange,
  readOnly,
}: {
  mod: ModuleSchema
  moduleValue: Record<string, ModuleFieldConfig>
  onChange: (next: Record<string, ModuleFieldConfig>) => void
  readOnly: boolean
}) {
  const [open, setOpen] = useState(false)
  const fields = Object.entries(mod.permission_schema)
  if (fields.length === 0) return null

  // Count configured fields (any access != none)
  const configured = fields.filter(([k]) => (moduleValue[k]?.access ?? 'none') !== 'none').length

  return (
    <div className="border rounded-lg overflow-hidden mb-3">
      <button
        type="button"
        className="w-full flex items-center gap-3 px-4 py-3 bg-gray-50 hover:bg-gray-100 transition-colors text-left"
        onClick={() => setOpen(v => !v)}
      >
        <span className="text-lg">{mod.icon}</span>
        <div className="flex-1">
          <div className="text-sm font-semibold text-gray-800">{mod.label}</div>
          <div className="text-xs text-gray-400">{fields.length} permissões</div>
        </div>
        {configured > 0 && (
          <span className="bg-blue-100 text-blue-700 text-xs px-2 py-0.5 rounded-full font-medium">
            {configured} ativo{configured > 1 ? 's' : ''}
          </span>
        )}
        <span className="text-gray-400 text-xs ml-2">{open ? '▲' : '▼'}</span>
      </button>

      {open && (
        <div className="px-4 bg-white">
          {fields.map(([fieldKey, fieldSchema]) => {
            const defaultField: ModuleFieldConfig = {
              access: fieldSchema.default ?? 'none',
              scope: [],
            }
            const current = moduleValue[fieldKey] ?? defaultField

            return (
              <FieldRow
                key={fieldKey}
                fieldKey={fieldKey}
                schema={fieldSchema}
                fieldConfig={current}
                onChange={next => {
                  onChange({ ...moduleValue, [fieldKey]: next })
                }}
                readOnly={readOnly}
              />
            )
          })}
        </div>
      )}
    </div>
  )
}

// ── ModulePermissionForm ──────────────────────────────────────────────────────

interface ModulePermissionFormProps {
  modules: ModuleSchema[]
  value: ModuleConfig
  onChange: (next: ModuleConfig) => void
  readOnly?: boolean
  className?: string
}

export function ModulePermissionForm({
  modules,
  value,
  onChange,
  readOnly = false,
  className = '',
}: ModulePermissionFormProps) {
  const active = modules.filter(m => m.active && Object.keys(m.permission_schema).length > 0)

  if (active.length === 0) {
    return (
      <div className={`text-sm text-gray-400 text-center py-8 ${className}`}>
        Nenhum módulo com permissões configuráveis encontrado.
      </div>
    )
  }

  return (
    <div className={className}>
      {active.map(mod => (
        <ModuleSection
          key={mod.module_id}
          mod={mod}
          moduleValue={value[mod.module_id] ?? {}}
          onChange={next => onChange({ ...value, [mod.module_id]: next })}
          readOnly={readOnly}
        />
      ))}
    </div>
  )
}

export default ModulePermissionForm
