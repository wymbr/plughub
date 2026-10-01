/**
 * A2ADescriptorEditor — o contrato do pool no canal `a2a` (AAS-01; adr-a2a-server-binding D3).
 *
 * O AgentCard é PROJEÇÃO deste bloco (D2): não existe card escrito à mão, então é aqui que o
 * operador diz o que o agente faz, o que precisa receber, o que devolve e quem pode chamá-lo.
 * O registry recusa pool de contato com `a2a` sem o bloco e o bloco sem o canal; a tela pede os
 * mesmos campos e diz o que falta antes do salvar.
 *
 * Os JSON Schemas são editados como TEXTO e só viram objeto quando o texto é JSON com `type`
 * (a mesma regra do schema: contrato vazio não diz nada ao chamador). Texto inválido marca o
 * descritor inválido e o salvar avisa — nunca envia um schema que não foi o que se escreveu.
 */
import React, { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import type { A2APrincipalKind, A2ASkill, PoolA2ADescriptor } from '@/types'

const PRINCIPAL_KINDS: A2APrincipalKind[] = ['partner', 'customer_agent']

export const EMPTY_A2A: PoolA2ADescriptor = {
  display_name: '', description: '',
  input_schema: { type: 'object', properties: {} },
  output_schema: { type: 'object', properties: {} },
  skills: [{ id: '', name: '', description: '', tags: [], examples: [] }],
  discoverable: false,
  principal_kinds: ['partner'],
}

/** O que falta para o registry aceitar — a mesma regra do `PoolA2ADescriptorSchema`. */
export function a2aProblems(d: PoolA2ADescriptor, schemaTextOk: boolean): string[] {
  const p: string[] = []
  if (!d.display_name.trim()) p.push('displayName')
  if (!d.description.trim()) p.push('description')
  if (!schemaTextOk) p.push('schemas')
  if (d.skills.length === 0) p.push('skills')
  const ids = d.skills.map(s => s.id)
  if (d.skills.some(s => !/^[a-z0-9_]+$/.test(s.id) || !s.name.trim() || !s.description.trim())
      || new Set(ids).size !== ids.length) p.push('skillFields')
  if (d.principal_kinds.length === 0) p.push('principals')
  return p
}

function schemaFromText(text: string): Record<string, unknown> | null {
  try {
    const v = JSON.parse(text) as unknown
    return v && typeof v === 'object' && !Array.isArray(v) && typeof (v as Record<string, unknown>).type === 'string'
      ? v as Record<string, unknown> : null
  } catch {
    return null
  }
}

const inputCls = 'w-full text-sm border border-border-strong rounded px-2 py-1 focus:outline-none focus:ring-1 focus:ring-primary/40'

export function A2ADescriptorEditor({
  value, onChange,
}: {
  value: PoolA2ADescriptor | null
  onChange: (d: PoolA2ADescriptor, schemaTextOk: boolean) => void
}) {
  const { t } = useTranslation('configRecursos')
  const d = value ?? EMPTY_A2A
  const [inText, setInText] = useState(() => JSON.stringify(d.input_schema, null, 2))
  const [outText, setOutText] = useState(() => JSON.stringify(d.output_schema, null, 2))
  const inOk = schemaFromText(inText) !== null
  const outOk = schemaFromText(outText) !== null

  // um pool novo com o canal marcado nasce com o descritor vazio já no formulário
  useEffect(() => { if (value === null) onChange(EMPTY_A2A, true) }, [value, onChange])

  const set = (patch: Partial<PoolA2ADescriptor>, ok = inOk && outOk) => onChange({ ...d, ...patch }, ok)
  const setSkill = (i: number, patch: Partial<A2ASkill>) =>
    set({ skills: d.skills.map((s, j) => (j === i ? { ...s, ...patch } : s)) })

  const onSchema = (which: 'input_schema' | 'output_schema', text: string) => {
    if (which === 'input_schema') setInText(text); else setOutText(text)
    const parsed = schemaFromText(text)
    const otherOk = which === 'input_schema' ? outOk : inOk
    onChange(parsed ? { ...d, [which]: parsed } : d, parsed !== null && otherOk)
  }

  const problems = a2aProblems(d, inOk && outOk)

  return (
    <div className="mt-3 border border-border rounded p-3 space-y-3">
      <div>
        <p className="text-xs font-semibold text-dark">{t('pools.a2a.label')}</p>
        <p className="text-2xs text-muted-light">{t('pools.a2a.hint')}</p>
      </div>

      <label className="block">
        <span className="text-xs font-medium text-dark">{t('pools.a2a.displayName')}</span>
        <input className={inputCls} value={d.display_name} onChange={e => set({ display_name: e.target.value })} />
      </label>
      <label className="block">
        <span className="text-xs font-medium text-dark">{t('pools.a2a.description')}</span>
        <span className="block text-2xs text-muted-light">{t('pools.a2a.descriptionHint')}</span>
        <textarea className={inputCls} rows={2} value={d.description}
                  onChange={e => set({ description: e.target.value })} />
      </label>

      {(['input_schema', 'output_schema'] as const).map(which => {
        const text = which === 'input_schema' ? inText : outText
        const ok = which === 'input_schema' ? inOk : outOk
        return (
          <label key={which} className="block">
            <span className="text-xs font-medium text-dark">{t(`pools.a2a.${which}`)}</span>
            <span className="block text-2xs text-muted-light">{t(`pools.a2a.${which}Hint`)}</span>
            <textarea className={`${inputCls} font-mono text-xs ${ok ? '' : 'border-red'}`} rows={5}
                      value={text} onChange={e => onSchema(which, e.target.value)} />
            {!ok && <span className="text-2xs text-red">{t('pools.a2a.schemaInvalid')}</span>}
          </label>
        )
      })}

      <div>
        <span className="text-xs font-medium text-dark">{t('pools.a2a.skills')}</span>
        <span className="block text-2xs text-muted-light mb-1">{t('pools.a2a.skillsHint')}</span>
        {d.skills.map((s, i) => (
          <div key={i} className="border border-border rounded p-2 mb-2 space-y-1">
            <div className="flex gap-2">
              <input className={inputCls} placeholder={t('pools.a2a.skillId')} value={s.id}
                     onChange={e => setSkill(i, { id: e.target.value })} />
              <input className={inputCls} placeholder={t('pools.a2a.skillName')} value={s.name}
                     onChange={e => setSkill(i, { name: e.target.value })} />
              <button type="button" className="text-xs text-red underline shrink-0"
                      onClick={() => set({ skills: d.skills.filter((_, j) => j !== i) })}>
                {t('pools.a2a.removeSkill')}
              </button>
            </div>
            <input className={inputCls} placeholder={t('pools.a2a.skillDescription')} value={s.description}
                   onChange={e => setSkill(i, { description: e.target.value })} />
            <input className={inputCls} placeholder={t('pools.a2a.skillTags')} value={s.tags.join(', ')}
                   onChange={e => setSkill(i, { tags: e.target.value.split(',').map(x => x.trim()).filter(Boolean) })} />
            <textarea className={inputCls} rows={2} placeholder={t('pools.a2a.skillExamples')}
                      value={s.examples.join('\n')}
                      onChange={e => setSkill(i, { examples: e.target.value.split('\n').map(x => x.trim()).filter(Boolean) })} />
          </div>
        ))}
        <button type="button" className="text-xs text-primary underline"
                onClick={() => set({ skills: [...d.skills, { id: '', name: '', description: '', tags: [], examples: [] }] })}>
          {t('pools.a2a.addSkill')}
        </button>
      </div>

      <div>
        <span className="text-xs font-medium text-dark">{t('pools.a2a.principals')}</span>
        <span className="block text-2xs text-muted-light mb-1">{t('pools.a2a.principalsHint')}</span>
        {PRINCIPAL_KINDS.map(k => (
          <label key={k} className="flex items-center gap-2 cursor-pointer">
            <input type="checkbox" className="w-4 h-4 rounded accent-primary"
                   checked={d.principal_kinds.includes(k)}
                   onChange={() => set({
                     principal_kinds: d.principal_kinds.includes(k)
                       ? d.principal_kinds.filter(x => x !== k)
                       : PRINCIPAL_KINDS.filter(x => x === k || d.principal_kinds.includes(x)),
                   })} />
            <span className="text-xs text-dark">{t(`pools.a2a.principal.${k}`)}</span>
          </label>
        ))}
      </div>

      <label className="flex items-center gap-2 cursor-pointer">
        <input type="checkbox" className="w-4 h-4 rounded accent-primary" checked={d.discoverable}
               onChange={e => set({ discoverable: e.target.checked })} />
        <span className="text-xs text-dark">{t('pools.a2a.discoverable')}</span>
      </label>
      <p className="text-2xs text-muted-light">{t('pools.a2a.discoverableHint')}</p>

      {problems.length > 0 && (
        <p className="text-2xs text-red">
          {t('pools.a2a.missing', { list: problems.map(p => t(`pools.a2a.problem.${p}`)).join(' · ') })}
        </p>
      )}
    </div>
  )
}
