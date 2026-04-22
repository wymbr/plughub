/**
 * CalendarManager.tsx
 * CRUD de Calendários e Sets de Feriados (calendar-api port 3700).
 *
 * Two sub-tabs:
 *   Calendários   — list/create/edit/delete
 *   Sets de Feriados — list/create/edit/delete
 */
import React, { useState } from 'react'
import {
  useCalendars, useHolidaySets,
  createCalendar, updateCalendar, deleteCalendar,
  createHolidaySet, updateHolidaySet, deleteHolidaySet,
} from '../api/calendar-hooks'
import type { CalendarRecord, HolidaySet, WeeklySlot, Holiday } from '../api/calendar-hooks'

const DAYS = ['Seg', 'Ter', 'Qua', 'Qui', 'Sex', 'Sáb', 'Dom']
const TIMEZONES = ['America/Sao_Paulo', 'America/Manaus', 'America/Belem', 'America/Fortaleza', 'America/Recife', 'America/Cuiaba', 'UTC']

interface Props {
  orgId:    string
  tenantId: string
}

export function CalendarManager({ orgId, tenantId }: Props) {
  const [tab, setTab] = useState<'calendars' | 'holidays'>('calendars')

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      {/* Sub-tabs */}
      <div style={subTabBarStyle}>
        <SubTab active={tab === 'calendars'} onClick={() => setTab('calendars')}>📅 Calendários</SubTab>
        <SubTab active={tab === 'holidays'}  onClick={() => setTab('holidays')}>🎉 Sets de Feriados</SubTab>
      </div>

      <div style={{ flex: 1, overflow: 'hidden' }}>
        {tab === 'calendars' && <CalendarsList orgId={orgId} tenantId={tenantId} />}
        {tab === 'holidays'  && <HolidaySetsList orgId={orgId} tenantId={tenantId} />}
      </div>
    </div>
  )
}

function SubTab({ active, onClick, children }: { active: boolean; onClick: () => void; children: React.ReactNode }) {
  return (
    <button
      onClick={onClick}
      style={{
        padding: '8px 18px', fontSize: 13, fontWeight: active ? 600 : 400,
        background: 'none', border: 'none',
        borderBottom: active ? '2px solid #3b82f6' : '2px solid transparent',
        color: active ? '#93c5fd' : '#64748b', cursor: 'pointer',
      }}
    >
      {children}
    </button>
  )
}

// ─── Calendars ────────────────────────────────────────────────────────────────

function CalendarsList({ orgId, tenantId }: { orgId: string; tenantId: string }) {
  const { calendars, loading, error, reload } = useCalendars(orgId, tenantId)
  const [modal, setModal] = useState<'create' | CalendarRecord | null>(null)

  async function handleDelete(id: string) {
    if (!confirm('Remover este calendário?')) return
    await deleteCalendar(id).catch(e => alert(String(e)))
    reload()
  }

  return (
    <div style={listContainer}>
      {/* Toolbar */}
      <div style={toolbar}>
        <span style={{ color: '#94a3b8', fontSize: 13 }}>{calendars.length} calendário(s)</span>
        {loading && <span style={{ color: '#64748b', fontSize: 12 }}>⟳</span>}
        {error   && <span style={{ color: '#ef4444', fontSize: 12 }}>⚠ {error}</span>}
        <button style={btnCreate} onClick={() => setModal('create')}>+ Novo</button>
      </div>

      {/* List */}
      <div style={listBody}>
        {calendars.map(cal => (
          <div key={cal.id} style={itemRow}>
            <div style={{ flex: 1 }}>
              <div style={{ fontWeight: 600, color: '#e2e8f0', fontSize: 14 }}>{cal.name}</div>
              <div style={{ fontSize: 12, color: '#64748b', marginTop: 2 }}>
                {cal.timezone} · {cal.weekly_schedule.length} slot(s) · {cal.holiday_set_ids.length} set(s) de feriados
              </div>
              {cal.description && <div style={{ fontSize: 12, color: '#475569', marginTop: 2 }}>{cal.description}</div>}
            </div>
            <div style={{ display: 'flex', gap: 8 }}>
              <button style={btnEdit} onClick={() => setModal(cal)}>✏</button>
              <button style={btnDel} onClick={() => handleDelete(cal.id)}>✕</button>
            </div>
          </div>
        ))}
        {!loading && calendars.length === 0 && (
          <div style={emptyMsg}>Nenhum calendário cadastrado.</div>
        )}
      </div>

      {/* Modal */}
      {modal && (
        <CalendarModal
          initial={modal === 'create' ? null : modal}
          orgId={orgId}
          tenantId={tenantId}
          onClose={() => setModal(null)}
          onSaved={() => { setModal(null); reload() }}
        />
      )}
    </div>
  )
}

function CalendarModal({ initial, orgId, tenantId, onClose, onSaved }: {
  initial:  CalendarRecord | null
  orgId:    string
  tenantId: string
  onClose:  () => void
  onSaved:  () => void
}) {
  const [name,        setName]        = useState(initial?.name ?? '')
  const [description, setDescription] = useState(initial?.description ?? '')
  const [timezone,    setTimezone]    = useState(initial?.timezone ?? 'America/Sao_Paulo')
  const [slots,       setSlots]       = useState<WeeklySlot[]>(initial?.weekly_schedule ?? [])
  const [error,       setError]       = useState<string | null>(null)
  const [saving,      setSaving]      = useState(false)

  function addSlot() {
    setSlots(prev => [...prev, { day: 0, open_time: '08:00', close_time: '18:00' }])
  }

  function updateSlot(i: number, field: keyof WeeklySlot, value: string | number) {
    setSlots(prev => prev.map((s, idx) => idx === i ? { ...s, [field]: value } : s))
  }

  function removeSlot(i: number) {
    setSlots(prev => prev.filter((_, idx) => idx !== i))
  }

  async function handleSave() {
    if (!name.trim()) { setError('Nome obrigatório'); return }
    setSaving(true); setError(null)
    try {
      if (initial) {
        await updateCalendar(initial.id, { name, description, timezone, weekly_schedule: slots })
      } else {
        await createCalendar({ organization_id: orgId, tenant_id: tenantId, name, description, timezone, weekly_schedule: slots, holiday_set_ids: [] })
      }
      onSaved()
    } catch (e) { setError(String(e)) }
    finally { setSaving(false) }
  }

  return (
    <Modal title={initial ? 'Editar Calendário' : 'Novo Calendário'} onClose={onClose}>
      <FieldRow label="Nome">
        <input style={inputStyle} value={name} onChange={e => setName(e.target.value)} placeholder="ex: Horário Comercial SP" />
      </FieldRow>
      <FieldRow label="Descrição">
        <input style={inputStyle} value={description} onChange={e => setDescription(e.target.value)} placeholder="Opcional" />
      </FieldRow>
      <FieldRow label="Fuso horário">
        <select style={inputStyle} value={timezone} onChange={e => setTimezone(e.target.value)}>
          {TIMEZONES.map(tz => <option key={tz} value={tz}>{tz}</option>)}
        </select>
      </FieldRow>

      {/* Weekly schedule */}
      <div style={{ marginTop: 16 }}>
        <div style={{ fontSize: 12, fontWeight: 600, color: '#64748b', textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: 8 }}>
          Horário semanal
        </div>
        {slots.map((slot, i) => (
          <div key={i} style={{ display: 'flex', gap: 8, marginBottom: 6, alignItems: 'center' }}>
            <select style={{ ...inputStyle, flex: '0 0 70px' }} value={slot.day} onChange={e => updateSlot(i, 'day', Number(e.target.value))}>
              {DAYS.map((d, idx) => <option key={idx} value={idx}>{d}</option>)}
            </select>
            <input style={{ ...inputStyle, flex: '0 0 80px' }} type="time" value={slot.open_time}  onChange={e => updateSlot(i, 'open_time',  e.target.value)} />
            <span style={{ color: '#64748b', fontSize: 12 }}>até</span>
            <input style={{ ...inputStyle, flex: '0 0 80px' }} type="time" value={slot.close_time} onChange={e => updateSlot(i, 'close_time', e.target.value)} />
            <button style={btnDel} onClick={() => removeSlot(i)}>✕</button>
          </div>
        ))}
        <button style={btnSecondary} onClick={addSlot}>+ Adicionar horário</button>
      </div>

      {error && <div style={errStyle}>⚠ {error}</div>}
      <div style={modalFooter}>
        <button style={btnCreate} onClick={handleSave} disabled={saving}>{saving ? 'Salvando…' : 'Salvar'}</button>
        <button style={btnSecondary} onClick={onClose}>Cancelar</button>
      </div>
    </Modal>
  )
}

// ─── Holiday Sets ─────────────────────────────────────────────────────────────

function HolidaySetsList({ orgId, tenantId }: { orgId: string; tenantId: string }) {
  const { sets, loading, error, reload } = useHolidaySets(orgId, tenantId)
  const [modal, setModal] = useState<'create' | HolidaySet | null>(null)

  async function handleDelete(id: string) {
    if (!confirm('Remover este set de feriados?')) return
    await deleteHolidaySet(id).catch(e => alert(String(e)))
    reload()
  }

  return (
    <div style={listContainer}>
      <div style={toolbar}>
        <span style={{ color: '#94a3b8', fontSize: 13 }}>{sets.length} set(s)</span>
        {loading && <span style={{ color: '#64748b', fontSize: 12 }}>⟳</span>}
        {error   && <span style={{ color: '#ef4444', fontSize: 12 }}>⚠ {error}</span>}
        <button style={btnCreate} onClick={() => setModal('create')}>+ Novo</button>
      </div>

      <div style={listBody}>
        {sets.map(set => (
          <div key={set.id} style={itemRow}>
            <div style={{ flex: 1 }}>
              <div style={{ fontWeight: 600, color: '#e2e8f0', fontSize: 14 }}>
                {set.name} {set.year ? <span style={{ fontSize: 12, color: '#64748b' }}>({set.year})</span> : null}
              </div>
              <div style={{ fontSize: 12, color: '#64748b', marginTop: 2 }}>{set.holidays.length} feriado(s)</div>
              {set.description && <div style={{ fontSize: 12, color: '#475569', marginTop: 2 }}>{set.description}</div>}
            </div>
            <div style={{ display: 'flex', gap: 8 }}>
              <button style={btnEdit} onClick={() => setModal(set)}>✏</button>
              <button style={btnDel}  onClick={() => handleDelete(set.id)}>✕</button>
            </div>
          </div>
        ))}
        {!loading && sets.length === 0 && (
          <div style={emptyMsg}>Nenhum set de feriados cadastrado.</div>
        )}
      </div>

      {modal && (
        <HolidaySetModal
          initial={modal === 'create' ? null : modal}
          orgId={orgId}
          tenantId={tenantId}
          onClose={() => setModal(null)}
          onSaved={() => { setModal(null); reload() }}
        />
      )}
    </div>
  )
}

function HolidaySetModal({ initial, orgId, tenantId, onClose, onSaved }: {
  initial:  HolidaySet | null
  orgId:    string
  tenantId: string
  onClose:  () => void
  onSaved:  () => void
}) {
  const [name,        setName]        = useState(initial?.name ?? '')
  const [description, setDescription] = useState(initial?.description ?? '')
  const [year,        setYear]        = useState<string>(initial?.year ? String(initial.year) : '')
  const [holidays,    setHolidays]    = useState<Holiday[]>(initial?.holidays ?? [])
  const [error,       setError]       = useState<string | null>(null)
  const [saving,      setSaving]      = useState(false)

  function addHoliday() {
    setHolidays(prev => [...prev, { date: '', name: '', description: '' }])
  }

  function updateHoliday(i: number, field: keyof Holiday, value: string) {
    setHolidays(prev => prev.map((h, idx) => idx === i ? { ...h, [field]: value } : h))
  }

  function removeHoliday(i: number) {
    setHolidays(prev => prev.filter((_, idx) => idx !== i))
  }

  async function handleSave() {
    if (!name.trim()) { setError('Nome obrigatório'); return }
    const yearNum = year ? parseInt(year) : null
    setSaving(true); setError(null)
    try {
      if (initial) {
        await updateHolidaySet(initial.id, { name, description, year: yearNum, holidays })
      } else {
        await createHolidaySet({ organization_id: orgId, tenant_id: tenantId, name, description, year: yearNum, holidays })
      }
      onSaved()
    } catch (e) { setError(String(e)) }
    finally { setSaving(false) }
  }

  return (
    <Modal title={initial ? 'Editar Set de Feriados' : 'Novo Set de Feriados'} onClose={onClose}>
      <FieldRow label="Nome">
        <input style={inputStyle} value={name} onChange={e => setName(e.target.value)} placeholder="ex: Feriados Nacionais 2026" />
      </FieldRow>
      <FieldRow label="Descrição">
        <input style={inputStyle} value={description} onChange={e => setDescription(e.target.value)} placeholder="Opcional" />
      </FieldRow>
      <FieldRow label="Ano">
        <input style={{ ...inputStyle, width: 100 }} type="number" value={year} onChange={e => setYear(e.target.value)} placeholder="ex: 2026" />
      </FieldRow>

      {/* Holidays */}
      <div style={{ marginTop: 16 }}>
        <div style={{ fontSize: 12, fontWeight: 600, color: '#64748b', textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: 8 }}>
          Feriados
        </div>
        {holidays.map((h, i) => (
          <div key={i} style={{ display: 'flex', gap: 8, marginBottom: 6, alignItems: 'center' }}>
            <input style={{ ...inputStyle, flex: '0 0 120px' }} type="date" value={h.date}        onChange={e => updateHoliday(i, 'date',        e.target.value)} />
            <input style={{ ...inputStyle, flex: 1           }} type="text" value={h.name}        onChange={e => updateHoliday(i, 'name',        e.target.value)} placeholder="Nome" />
            <input style={{ ...inputStyle, flex: 1           }} type="text" value={h.description} onChange={e => updateHoliday(i, 'description', e.target.value)} placeholder="Descrição" />
            <button style={btnDel} onClick={() => removeHoliday(i)}>✕</button>
          </div>
        ))}
        <button style={btnSecondary} onClick={addHoliday}>+ Adicionar feriado</button>
      </div>

      {error && <div style={errStyle}>⚠ {error}</div>}
      <div style={modalFooter}>
        <button style={btnCreate} onClick={handleSave} disabled={saving}>{saving ? 'Salvando…' : 'Salvar'}</button>
        <button style={btnSecondary} onClick={onClose}>Cancelar</button>
      </div>
    </Modal>
  )
}

// ─── Shared components ────────────────────────────────────────────────────────

function Modal({ title, onClose, children }: { title: string; onClose: () => void; children: React.ReactNode }) {
  return (
    <div style={overlay}>
      <div style={modalBox}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 20 }}>
          <h3 style={{ margin: 0, fontSize: 16, fontWeight: 700, color: '#e2e8f0' }}>{title}</h3>
          <button style={{ background: 'none', border: 'none', color: '#64748b', cursor: 'pointer', fontSize: 18 }} onClick={onClose}>✕</button>
        </div>
        <div style={{ maxHeight: '60vh', overflowY: 'auto', paddingRight: 4 }}>
          {children}
        </div>
      </div>
    </div>
  )
}

function FieldRow({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div style={{ marginBottom: 12 }}>
      <label style={{ display: 'block', fontSize: 12, fontWeight: 600, color: '#94a3b8', marginBottom: 4 }}>{label}</label>
      {children}
    </div>
  )
}

// ─── Styles ───────────────────────────────────────────────────────────────────

const listContainer: React.CSSProperties = { display: 'flex', flexDirection: 'column', height: '100%' }
const toolbar: React.CSSProperties = { display: 'flex', alignItems: 'center', gap: 10, padding: '12px 20px', borderBottom: '1px solid #1e293b', flexShrink: 0 }
const listBody: React.CSSProperties = { flex: 1, overflowY: 'auto' }
const itemRow: React.CSSProperties = { display: 'flex', alignItems: 'center', padding: '14px 20px', borderBottom: '1px solid #1e293b', gap: 12 }
const emptyMsg: React.CSSProperties = { padding: '40px 24px', color: '#475569', textAlign: 'center', fontSize: 14 }
const subTabBarStyle: React.CSSProperties = { display: 'flex', borderBottom: '1px solid #1e293b', backgroundColor: '#0a1628', flexShrink: 0 }
const overlay: React.CSSProperties = { position: 'fixed', inset: 0, backgroundColor: 'rgba(0,0,0,0.6)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 100 }
const modalBox: React.CSSProperties = { backgroundColor: '#1e293b', borderRadius: 10, padding: 24, width: 540, maxWidth: '95vw', boxShadow: '0 20px 60px rgba(0,0,0,0.6)' }
const modalFooter: React.CSSProperties = { display: 'flex', gap: 10, marginTop: 20, justifyContent: 'flex-end' }
const errStyle: React.CSSProperties = { marginTop: 10, fontSize: 12, color: '#ef4444' }
const inputStyle: React.CSSProperties = { width: '100%', background: '#0f172a', border: '1px solid #334155', borderRadius: 6, color: '#e2e8f0', fontSize: 13, padding: '6px 10px', outline: 'none', boxSizing: 'border-box' }
const btnCreate: React.CSSProperties = { background: '#1e40af', border: 'none', color: '#e2e8f0', borderRadius: 6, padding: '5px 14px', cursor: 'pointer', fontSize: 13, fontWeight: 600, marginLeft: 'auto' }
const btnEdit: React.CSSProperties = { background: 'none', border: '1px solid #334155', color: '#94a3b8', borderRadius: 6, padding: '4px 10px', cursor: 'pointer', fontSize: 12 }
const btnDel: React.CSSProperties = { background: 'none', border: '1px solid #ef444466', color: '#ef4444', borderRadius: 6, padding: '4px 10px', cursor: 'pointer', fontSize: 12 }
const btnSecondary: React.CSSProperties = { background: 'none', border: '1px solid #334155', color: '#94a3b8', borderRadius: 6, padding: '4px 12px', cursor: 'pointer', fontSize: 12 }
