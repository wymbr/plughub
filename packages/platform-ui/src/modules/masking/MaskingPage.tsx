/**
 * MaskingPage — /config/masking
 *
 * Dedicated UI for message masking configuration.
 * Settings are stored in Config API (namespace "audit_policy") and read by
 * MaskingService in mcp-server-plughub via Redis cache fallback chain.
 *
 * Sections:
 *   1. Access Policy — who can see original_content (unmasked values)
 *   2. Audit Capture — whether to capture input/output in audit records
 *   3. Retention     — how long masked tokens are kept in Redis
 *   4. Rules overview — read-only list of DEFAULT_MASKING_RULES categories
 */
import React, { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Users, ClipboardList, Timer, Monitor, Archive, Lock, Check, X, AlertTriangle, Pencil } from 'lucide-react'
import { useAuth } from '@/auth/useAuth'
import { useNamespace, useProvenance, putConfig } from '../config-plataforma/api/config-hooks'
import type { KeyProvenance } from '../config-plataforma/api/config-hooks'
import type { TokenDisplayMode, EchoMode, MaskingDisplayRule } from '@/components/MaskedToken'
import { DEFAULT_DISPLAY_RULE } from '@/components/MaskedToken'

// ── Catálogo de tipos (mirror de DataTypeCatalog em @plughub/schemas/audit.ts) ──
//
// ⚠️ Aqui existia `DEFAULT_CATEGORIES`, uma lista HARDCODED de 6 categorias cujo
// comentário prometia "mirrors DEFAULT_MASKING_RULES" e não espelhava: `iban` e
// `passport` não existem no enum DataCategory, não têm regex e não têm regra
// nenhuma — e a tela lhes dava selo "Ativo" incondicional, além de oferecer editor
// de regra de canal que gravava numa chave que ninguém lê. Era o 3º de SETE
// inventários de categoria do repositório.
//
// A lista agora VEM DO DADO (`masking.types` no config-api). Nenhuma categoria pode
// aparecer nesta tela sem estar declarada, e o selo é DERIVADO do que o tipo tem.

// ⚠️ Cópia local de `LgpdClassSchema` (@plughub/schemas/audit.ts). A deduplicação
// exige o platform-ui depender de @plughub/schemas — dívida registrada no TODO.md.
// Enquanto for cópia, ela ACOMPANHA o canônico: `nao_classificado` entrou na T1.
type LgpdClass = 'pessoal' | 'sensivel' | 'financeiro' | 'credencial' | 'none' | 'nao_classificado'

interface DataTypeFormat {
  display?:              string
  detect_pattern?:       string
  replacement?:          string
  preserve_last_digits?: number
  preserve_pattern?:     string
}

interface DataTypeEntry {
  id:       string
  label?:   string
  icon?:    string
  formato?: DataTypeFormat
  mascara?: { by_role?: Record<string, string>; display?: MaskingDisplayRule }
  lgpd?:    LgpdClass
}

interface ContextMapLeaf { tipo: string; legado?: string[]; label?: string }
interface ContextMapDoc {
  mode?:             string
  dynamic_prefixes?: string[]
  contexto:          Record<string, Record<string, Record<string, ContextMapLeaf>>>
}

/** Root que só a plataforma declara — a tela o mostra em SOMENTE LEITURA. Editar uma
 *  folha do core aqui criaria drift contra o `seed.py`, que é o dono dela, e o gate
 *  `probe_seed_drift_named` passaria a acusar uma divergência que a própria tela criou. */
const PLATFORM_ROOT = 'core'

interface DataTypeCatalog { types: DataTypeEntry[] }

const ROLES_OPTIONS = ['evaluator', 'reviewer', 'supervisor', 'admin', 'developer']

// ── Helpers ──────────────────────────────────────────────────────────────────

function badge(text: string, color: string) {
  return (
    <span style={{
      display: 'inline-block', padding: '2px 8px', borderRadius: 12,
      fontSize: 11, fontWeight: 600, background: color + '22', color,
      border: `1px solid ${color}44`,
    }}>{text}</span>
  )
}

// ── ContextStore masking types (mirrors @plughub/schemas) ────────────────────

type ContextMaskingType =
  | 'plain' | 'hidden' | 'full' | 'last_2' | 'last_4'
  | 'first_1' | 'first_word' | 'email_domain' | 'financial'

interface ContextMaskingRule {
  pattern: string
  role:    'operator' | 'supervisor' | '*'
  type:    ContextMaskingType
  label?:  string
}

interface ContextMaskingConfig {
  rules:                       ContextMaskingRule[]
  default_unmatched_operator:  ContextMaskingType
  /** Roles treated as "supervisor" (bypass namespace gate, see PII plain). */
  supervisor_roles?:           string[]
}

const DEFAULT_SUPERVISOR_ROLES = ['supervisor', 'admin', 'evaluator', 'reviewer']

const MASKING_TYPE_INFO: Record<ContextMaskingType, { label: string; sample: string }> = {
  plain:        { label: 'Visível (sem máscara)',  sample: '11.222.333-45' },
  hidden:       { label: 'Oculto (remove campo)', sample: '(oculto)' },
  full:         { label: 'Totalmente mascarado',  sample: '***' },
  last_2:       { label: 'Últimos 2 dígitos',     sample: '***-45' },
  last_4:       { label: 'Últimos 4 dígitos',     sample: '****3345' },
  first_1:      { label: 'Primeira letra',        sample: 'J***' },
  first_word:   { label: 'Primeira palavra',      sample: 'João ***' },
  email_domain: { label: 'Domínio visível',       sample: 'j***@empresa.com' },
  financial:    { label: 'Valor financeiro',      sample: 'R$ ****,**' },
}

const EMPTY_CONTEXT_CONFIG: ContextMaskingConfig = {
  rules: [],
  default_unmatched_operator: 'plain',
  supervisor_roles: DEFAULT_SUPERVISOR_ROLES,
}

/**
 * Os papéis que `mascara.by_role` endereça. É a MESMA partição de
 * `ContextMaskingRule.role` (@plughub/schemas) menos o curinga `*`, que não faz
 * sentido aqui: o mapa já é por papel, e uma entrada `*` conviveria com as duas
 * específicas sem regra de precedência declarada — duas respostas para a mesma
 * pergunta, que é o defeito que o catálogo de tipos existe para fechar.
 */
const MASK_ROLES = ['operator', 'supervisor'] as const

/** Ordem de exibição das classes; espelha `LgpdClassSchema`. */
const LGPD_CLASSES: LgpdClass[] = [
  'none', 'pessoal', 'sensivel', 'financeiro', 'credencial', 'nao_classificado',
]

/**
 * Espelho de `typeMasksSomething` (@plughub/schemas/audit.ts) — **derivado, nunca
 * lista de exceção**, e a fórmula é copiada literalmente porque quem julga de
 * verdade é o portão de deploy da T5 (`invalid_masked_type`): um tipo que não
 * mascara para papel nenhum é INELEGÍVEL a `masked:` numa skill.
 *
 * Existe na tela para que a consequência apareça ANTES do save. Sem ele, esvaziar
 * o `by_role` de um tipo em uso faz o próximo deploy do skill ser recusado com um
 * erro que aponta para o YAML, e não para a edição que o causou.
 */
function typeMasksSomething(dt: DataTypeEntry): boolean {
  return Object.values(dt.mascara?.by_role ?? {}).some(v => v !== 'plain')
}

// ── Main component ────────────────────────────────────────────────────────────

export default function MaskingPage() {
  const { t } = useTranslation('masking')
  // G-PROBE platform-wide: escritas usam o Bearer do operador + ABAC `config.masking` —
  // sem caixa de admin-token. `adminToken` mantém o nome (=accessToken) p/ diff mínimo.
  const { tenantId, session } = useAuth()
  const adminToken = session?.accessToken ?? ''
  const [saving,       setSaving]       = useState<string | null>(null)
  const [toast,        setToast]        = useState<{ msg: string; ok: boolean } | null>(null)

  // audit_policy namespace is the canonical source for masking/audit configuration
  const { entries, loading, error, reload } = useNamespace(tenantId, 'audit_policy')

  // masking namespace stores per-category display rules
  const { entries: maskingEntries, reload: reloadMasking } = useNamespace(tenantId, 'masking')

  // ALW-06 — proveniência dos DOIS namespaces que esta tela escreve. Não é enfeite:
  // todo `putConfig` daqui manda `tenantId`, ou seja, a primeira gravação de uma key
  // CRIA um override e desliga aquele tenant das atualizações da plataforma, para
  // sempre e em silêncio. Medido em `tenant_demo`: `masking.types` acabou com uma
  // cópia byte-idêntica do global (mesmo md5) — informação zero, e engole toda edição
  // futura; `masking.context_rules` já divergiu de fato.
  const provMasking = useProvenance(tenantId, 'masking')
  const provAudit   = useProvenance(tenantId, 'audit_policy')

  // Resolved values with defaults — entries[key].value holds the actual config value
  const val = (key: string): unknown => entries[key]?.value ?? entries[key]

  const authorizedRoles: string[] = Array.isArray(val('authorized_roles'))
    ? (val('authorized_roles') as string[])
    : ['evaluator', 'reviewer']

  const captureInput:  boolean = val('capture_input')  === true
  const captureOutput: boolean = val('capture_output') === true
  const retentionDays: number  = typeof val('token_retention_days') === 'number'
    ? (val('token_retention_days') as number)
    : 30

  function showToast(msg: string, ok: boolean) {
    setToast({ msg, ok })
    setTimeout(() => setToast(null), 3000)
  }

  async function saveKey(key: string, value: unknown) {
    if (!adminToken) { showToast(t('toast.tokenRequired'), false); return }
    setSaving(key)
    try {
      await putConfig('audit_policy', key, value, tenantId, '', adminToken)
      reload()
      provAudit.reload()   // a 1a gravacao CRIA o override — o banner muda junto
      showToast(t('toast.keySaved', { key }), true)
    } catch (e) {
      showToast(String(e), false)
    } finally {
      setSaving(null)
    }
  }

  // ── Catálogo de tipos — a lista que esta tela mostra ────────────────────────

  const rawCatalog = maskingEntries['types']?.value ?? maskingEntries['types']
  const catalog: DataTypeCatalog = (
    rawCatalog && typeof rawCatalog === 'object' && Array.isArray((rawCatalog as DataTypeCatalog).types)
  ) ? (rawCatalog as DataTypeCatalog) : { types: [] }
  const dataTypes = catalog.types

  /**
   * A regra de canal mora NO TIPO (`mascara.display`). Gravar é reescrever o
   * catálogo inteiro — mesmo padrão de `saveContextRules` logo abaixo, e é o que
   * mantém UMA casa em vez de duas.
   *
   * ⚠️ As chaves legadas `rule.{category}` foram FECHADAS na fase V2b (2026-08-29):
   * esta tela já não escrevia nelas desde a V2, e agora ninguém mais as lê. Havia
   * uma armadilha ARMADA no caminho: `getMaskingRule` devolvia o override legado e
   * `update()` o gravava de volta no CATÁLOGO — editar qualquer chave de uma
   * categoria com override PROMOVIA o legado a tipo, em silêncio. Zero dano medido
   * (não existia nenhuma chave), mas o leitor não era peso morto.
   */
  /**
   * Grava uma mudança em UM tipo do catálogo, reescrevendo o catálogo inteiro.
   *
   * Ponto único de escrita das QUATRO dimensões do tipo (CNS-07, 2026-09-01) —
   * até aqui só `mascara.display` era editável, e `mascara.by_role` (o que cada
   * PAPEL enxerga) não tinha superfície nenhuma, apesar de ser a dimensão que a
   * política de masking realmente consome. `lgpd` era exibida como selo e não
   * podia ser corrigida pela tela.
   *
   * A escrita é sempre do catálogo INTEIRO porque a chave `masking.types` é um
   * documento só; um PATCH por tipo criaria uma segunda casa para a mesma verdade.
   */
  async function saveType(category: string, mutate: (dt: DataTypeEntry) => DataTypeEntry) {
    if (!adminToken) { showToast(t('toast.tokenRequired'), false); return }
    setSaving(`type.${category}`)
    try {
      const next: DataTypeCatalog = {
        types: dataTypes.map(dt => dt.id === category ? mutate(dt) : dt),
      }
      await putConfig('masking', 'types', next, tenantId, '', adminToken)
      reloadMasking()
      provMasking.reload()
      showToast(t('toast.keySaved', { key: `types.${category}` }), true)
    } catch (e) {
      showToast(String(e), false)
    } finally {
      setSaving(null)
    }
  }

  async function saveMaskingRule(category: string, rule: MaskingDisplayRule) {
    await saveType(category, dt => ({ ...dt, mascara: { ...(dt.mascara ?? {}), display: rule } }))
  }

  /**
   * `by_role` é um MAPA papel → tipo de máscara, e a ausência da chave não é o
   * mesmo que `plain`: ausente significa *"este papel não tem regra própria"*,
   * enquanto `plain` é a decisão explícita de mostrar em claro. Por isso o valor
   * vazio do select REMOVE a chave em vez de gravar `"plain"` — gravar o default
   * apagaria a distinção, que é o padrão que este repositório cataloga.
   */
  async function saveByRole(category: string, role: string, type: ContextMaskingType | '') {
    await saveType(category, dt => {
      const by = { ...(dt.mascara?.by_role ?? {}) }
      if (type === '') delete by[role]
      else            by[role] = type
      return { ...dt, mascara: { ...(dt.mascara ?? {}), by_role: by } }
    })
  }

  async function saveLgpd(category: string, lgpd: LgpdClass) {
    await saveType(category, dt => ({ ...dt, lgpd }))
  }

  function getMaskingRule(category: string): MaskingDisplayRule {
    // Uma casa só: o TIPO, depois o default. (V2b fechou o override legado.)
    const fromType = dataTypes.find(dt => dt.id === category)?.mascara?.display
    if (fromType && typeof fromType === 'object') return fromType
    return DEFAULT_DISPLAY_RULE
  }

  // ── Context Store masking rules ─────────────────────────────────────────────

  // O estado do mapa saiu junto com a secao (ALW-03) — vive em
  // `modules/context-map/ContextMapPage.tsx`. `PLATFORM_ROOT` e o tipo `ContextMapDoc`
  // seguem aqui porque a pagina nova os redeclara: sao 6 linhas, e o platform-ui ainda
  // nao depende de `@plughub/schemas` (divida ja registrada no TODO.md).

  const rawContextRules = maskingEntries['context_rules']?.value ?? maskingEntries['context_rules']
  const contextRulesConfig: ContextMaskingConfig = (
    rawContextRules && typeof rawContextRules === 'object'
  ) ? (rawContextRules as ContextMaskingConfig) : EMPTY_CONTEXT_CONFIG

  async function saveContextRules(config: ContextMaskingConfig) {
    if (!adminToken) { showToast(t('toast.tokenRequired'), false); return }
    setSaving('context_rules')
    try {
      await putConfig('masking', 'context_rules', config, tenantId, '', adminToken)
      reloadMasking()
      provMasking.reload()
      showToast('Regras de ContextStore salvas com sucesso', true)
    } catch (e) {
      showToast(String(e), false)
    } finally {
      setSaving(null)
    }
  }

  function toggleRole(role: string) {
    const next = authorizedRoles.includes(role)
      ? authorizedRoles.filter(r => r !== role)
      : [...authorizedRoles, role]
    saveKey('authorized_roles', next)
  }

  return (
    <div style={{ height: '100%', overflowY: 'auto', background: '#0a1628', color: '#e2e8f0' }}>
      {/* Toast */}
      {toast && (
        <div style={{
          position: 'fixed', top: 16, right: 24, zIndex: 1000,
          background: toast.ok ? '#064e3b' : '#7f1d1d',
          border: `1px solid ${toast.ok ? '#10b981' : '#ef4444'}`,
          borderRadius: 8, padding: '10px 18px', fontSize: 13,
          color: toast.ok ? '#6ee7b7' : '#fca5a5', boxShadow: '0 4px 20px #0008',
        }}>
          {toast.ok ? <Check size={13} style={{ display: 'inline', marginRight: 6 }} aria-hidden="true" /> : <X size={13} style={{ display: 'inline', marginRight: 6 }} aria-hidden="true" />}{toast.msg}
        </div>
      )}

      {/* Page header */}
      <div style={{ padding: '20px 28px 16px', borderBottom: '1px solid #1e293b' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 16 }}>
          <div>
            <h1 style={{ margin: 0, fontSize: 18, fontWeight: 700, color: '#e2e8f0' }}>
              <Lock size={18} style={{ display: 'inline', marginRight: 8, verticalAlign: 'middle' }} aria-hidden="true" />{t('header.title')}
            </h1>
            <p style={{ margin: '4px 0 0', fontSize: 13, color: '#64748b' }}>
              {t('header.description')}
            </p>
          </div>
        </div>
      </div>

      {/* Loading / error */}
      {loading && <div style={infoBox('#1e293b', '#94a3b8')}>{t('loading')}</div>}
      {error   && <div style={{ ...infoBox('#7f1d1d22', '#fca5a5'), display: 'flex', alignItems: 'center', gap: 6 }}><AlertTriangle size={14} aria-hidden="true" />{error}</div>}

      <div style={{ padding: '20px 28px', display: 'flex', flexDirection: 'column', gap: 24 }}>

        {/* ── ALW-06: qual escopo está em vigor ────────────────────────────── */}
        <ProvenanceBanner
          tenantId={tenantId}
          groups={[
            { ns: 'masking',      ...provMasking },
            { ns: 'audit_policy', ...provAudit   },
          ]}
        />

        {/* ── Section 1: Access Policy ─────────────────────────────────────── */}
        <Section
          icon={Users}
          title={t('section.access.title')}
          desc={t('section.access.description')}
        >
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginTop: 12 }}>
            {ROLES_OPTIONS.map(role => {
              const active = authorizedRoles.includes(role)
              return (
                <button
                  key={role}
                  onClick={() => toggleRole(role)}
                  disabled={saving === 'authorized_roles'}
                  style={{
                    padding: '6px 16px', borderRadius: 20, fontSize: 13, fontWeight: 500,
                    cursor: 'pointer', transition: 'all 0.15s',
                    background: active ? '#1e3a5f' : '#0f172a',
                    border: active ? '1px solid #3b82f6' : '1px solid #334155',
                    color: active ? '#93c5fd' : '#64748b',
                  }}
                >
                  {active && <Check size={12} style={{ display: 'inline', marginRight: 4 }} aria-hidden="true" />}{role}
                </button>
              )
            })}
          </div>
          <p style={{ margin: '10px 0 0', fontSize: 12, color: '#475569' }}>
            {t('section.access.activeRoles', {
              roles: authorizedRoles.length === 0 ? t('section.access.noRoles') : authorizedRoles.join(', ')
            })}
          </p>
        </Section>

        {/* ── Section 2: Audit Capture ──────────────────────────────────────── */}
        <Section
          icon={ClipboardList}
          title={t('section.audit.title')}
          desc={t('section.audit.description')}
        >
          <div style={{ display: 'flex', gap: 16, marginTop: 12, flexWrap: 'wrap' }}>
            <ToggleCard
              label={t('section.audit.captureInput')}
              sublabel="capture_input"
              active={captureInput}
              onToggle={() => saveKey('capture_input', !captureInput)}
              saving={saving === 'capture_input'}
              warning={t('section.audit.warning')}
            />
            <ToggleCard
              label={t('section.audit.captureOutput')}
              sublabel="capture_output"
              active={captureOutput}
              onToggle={() => saveKey('capture_output', !captureOutput)}
              saving={saving === 'capture_output'}
            />
          </div>
        </Section>

        {/* ── Section 3: Token Retention ────────────────────────────────────── */}
        <Section
          icon={Timer}
          title={t('section.retention.title')}
          desc={t('section.retention.description')}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginTop: 12 }}>
            <RetentionEditor
              value={retentionDays}
              onSave={v => saveKey('token_retention_days', v)}
              saving={saving === 'token_retention_days'}
            />
          </div>
        </Section>

        {/* ── Section 4: Categories overview ───────────────────────────────── */}
        <Section
          icon={ClipboardList}
          title={t('section.categories.title')}
          desc={t('section.categories.description')}
        >
          {dataTypes.length === 0 && (
            <div style={{ marginTop: 12, padding: '10px 14px', background: '#1a1206', border: '1px solid #78350f', borderRadius: 8, fontSize: 12, color: '#fbbf24' }}>
              {t('section.categories.catalogMissing')}
            </div>
          )}
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(260px, 1fr))', gap: 10, marginTop: 12 }}>
            {dataTypes.map(dt => {
              // ⚠️ Selo DERIVADO do que o tipo tem, nunca constante. O selo "Ativo"
              // incondicional que vivia aqui é o que deixou `iban`/`passport` passarem
              // por categorias em vigor por meses.
              const detects = typeof dt.formato?.detect_pattern === 'string' && dt.formato.detect_pattern.length > 0
              return (
                <div key={dt.id} style={{
                  background: '#0f172a', border: '1px solid #1e293b', borderRadius: 8,
                  padding: '12px 16px', display: 'flex', alignItems: 'flex-start', gap: 12,
                }}>
                  <span style={{ fontSize: 22 }}>{dt.icon ?? '🔒'}</span>
                  <div>
                    <div style={{ fontWeight: 600, fontSize: 13, color: '#e2e8f0' }}>
                      {t(`categories.${dt.id}`, { defaultValue: dt.label ?? dt.id })}
                    </div>
                    {dt.formato?.display && (
                      <div style={{ fontSize: 11, color: '#64748b', marginTop: 2 }}>
                        {t('section.categories.display')}: <code style={{ color: '#94a3b8', background: '#1e293b', padding: '0 4px', borderRadius: 3 }}>{dt.formato.display}</code>
                      </div>
                    )}
                    <div style={{ display: 'flex', gap: 6, marginTop: 6, flexWrap: 'wrap' }}>
                      {detects
                        ? badge(t('section.categories.detects'), '#22c55e')
                        : badge(t('section.categories.declaredOnly'), '#94a3b8')}
                      {dt.lgpd && dt.lgpd !== 'none' && badge(
                        t(`lgpd.${dt.lgpd}`, { defaultValue: dt.lgpd }),
                        dt.lgpd === 'sensivel' ? '#dc2626' : dt.lgpd === 'credencial' ? '#a78bfa' : '#f59e0b',
                      )}
                    </div>
                  </div>
                </div>
              )
            })}
          </div>
          <div style={{ marginTop: 12, padding: '10px 14px', background: '#0f172a', borderRadius: 8, border: '1px solid #1e293b' }}>
            <p style={{ margin: 0, fontSize: 12, color: '#64748b', lineHeight: 1.6 }}>
              <strong style={{ color: '#94a3b8' }}>{t('section.categories.tokenFormat')}:</strong>{' '}
              <code style={{ color: '#7dd3fc', background: '#0c1a30', padding: '2px 6px', borderRadius: 3 }}>
                [category:tk_a8f3:display_partial]
              </code>
              {' '}— {t('section.categories.tokenExplanation')}
            </p>
          </div>
        </Section>

        {/*
          ── Section 5: o editor do TIPO ─────────────────────────────────────
          Era "Display Rules by Category" e editava só `mascara.display` — uma
          das QUATRO dimensões do tipo. A dimensão que a política de masking
          realmente consome (`mascara.by_role`) não tinha superfície nenhuma, e
          a classe LGPD só aparecia como selo. CNS-07 (2026-09-01).
        */}
        <Section
          icon={Monitor}
          title={t('section.typeEditor.title', { defaultValue: 'Data Types — mask, roles and class' })}
          desc={t('section.typeEditor.description', { defaultValue: 'Each type declares how the value is shown per channel, what each role sees, and its LGPD class. Changes apply immediately to new sessions.' })}
        >
          <div style={{ display: 'flex', flexDirection: 'column', gap: 12, marginTop: 12 }}>
            {dataTypes.map(cat => {
              const rule = getMaskingRule(cat.id)
              const isSaving = saving === `type.${cat.id}`
              function update(patch: Partial<MaskingDisplayRule>) {
                saveMaskingRule(cat.id, { ...rule, ...patch })
              }
              return (
                <div key={cat.id} style={{
                  background: '#0f172a', border: '1px solid #1e293b', borderRadius: 8,
                  padding: '14px 16px',
                }}>
                  {/* Category header */}
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 12 }}>
                    <span style={{ fontSize: 18 }}>{cat.icon ?? '🔒'}</span>
                    <span style={{ fontWeight: 600, fontSize: 13, color: '#e2e8f0' }}>
                      {t(`categories.${cat.id}`, { defaultValue: cat.label ?? cat.id })}
                    </span>
                    <code style={{ fontSize: 10, color: '#475569', marginLeft: 4 }}>{cat.id}</code>
                    {isSaving && <span style={{ fontSize: 10, color: '#3b82f6', marginLeft: 'auto' }}>saving…</span>}
                  </div>

                  {/* Controls row */}
                  <div style={{ display: 'flex', flexWrap: 'wrap', gap: 16, alignItems: 'flex-start' }}>

                    {/* token_display — CHANNEL-ABSTRACT (ALW-10). Absorveu o antigo
                        `display_voice`: a politica e do TIPO, a traducao e do adapter. */}
                    <div style={{ display: 'flex', flexDirection: 'column', gap: 4, minWidth: 150 }}>
                      <label style={labelStyle}>
                        {t('section.displayRules.token', { defaultValue: 'Masked token' })}
                      </label>
                      <select
                        value={rule.token_display}
                        disabled={isSaving}
                        onChange={e => update({ token_display: e.target.value as TokenDisplayMode })}
                        style={{ ...selectStyle }}
                      >
                        <option value="display_partial">{t('tokenDisplay.partial', { defaultValue: 'Partial (***-00)' })}</option>
                        <option value="full_mask">{t('tokenDisplay.full', { defaultValue: 'Full mask (•••••)' })}</option>
                        <option value="hidden">{t('tokenDisplay.hidden', { defaultValue: 'Label only' })}</option>
                      </select>
                    </div>

                    {/* echo_to_customer — ADVISORY: o cliente digitou o valor e ja o
                        conhece; a plataforma DECLARA o modo, o cliente do canal obedece. */}
                    <div style={{ display: 'flex', flexDirection: 'column', gap: 4, minWidth: 150 }}>
                      <label style={labelStyle}>
                        {t('section.displayRules.echoCustomer', { defaultValue: 'Echo to customer' })}
                      </label>
                      <EchoSelect
                        value={rule.echo_to_customer}
                        disabled={isSaving}
                        onChange={v => update({ echo_to_customer: v })}
                      />
                    </div>

                    {/* echo_to_operator — FRONTEIRA de confidencialidade: o operador nao
                        conhece o valor, e a plataforma controla as casas que o exibem. */}
                    <div style={{ display: 'flex', flexDirection: 'column', gap: 4, minWidth: 150 }}>
                      <label style={labelStyle}>
                        {t('section.displayRules.echoOperator', { defaultValue: 'Echo to operator' })}
                      </label>
                      <EchoSelect
                        value={rule.echo_to_operator}
                        disabled={isSaving}
                        onChange={v => update({ echo_to_operator: v })}
                      />
                    </div>

                    {/* ── by_role: o que cada PAPEL enxerga (CNS-07) ──────────── */}
                    {MASK_ROLES.map(role => (
                      <div key={role} style={{ display: 'flex', flexDirection: 'column', gap: 4, minWidth: 160 }}>
                        <label style={{ fontSize: 10, color: '#64748b', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.06em' }}>
                          {t(`section.typeEditor.role.${role}`, { defaultValue: role })}
                        </label>
                        <select
                          value={cat.mascara?.by_role?.[role] ?? ''}
                          disabled={isSaving}
                          onChange={e => saveByRole(cat.id, role, e.target.value as ContextMaskingType | '')}
                          style={{ ...selectStyle }}
                        >
                          <option value="">{t('section.typeEditor.noRule', { defaultValue: '— no rule —' })}</option>
                          {(Object.keys(MASKING_TYPE_INFO) as ContextMaskingType[]).map(mt => (
                            <option key={mt} value={mt}>
                              {t(`maskingType.${mt}`, { defaultValue: MASKING_TYPE_INFO[mt].label })}
                              {' · '}{MASKING_TYPE_INFO[mt].sample}
                            </option>
                          ))}
                        </select>
                      </div>
                    ))}

                    {/* ── lgpd: a CLASSE do dado ──────────────────────────────── */}
                    <div style={{ display: 'flex', flexDirection: 'column', gap: 4, minWidth: 160 }}>
                      <label style={{ fontSize: 10, color: '#64748b', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.06em' }}>
                        {t('section.typeEditor.lgpd', { defaultValue: 'LGPD class' })}
                      </label>
                      <select
                        value={cat.lgpd ?? 'none'}
                        disabled={isSaving}
                        onChange={e => saveLgpd(cat.id, e.target.value as LgpdClass)}
                        style={{ ...selectStyle }}
                      >
                        {LGPD_CLASSES.map(c => (
                          <option key={c} value={c}>{t(`lgpd.${c}`, { defaultValue: c })}</option>
                        ))}
                      </select>
                    </div>
                  </div>

                  {/*
                    Consequência DERIVADA, mostrada antes do save: um tipo sem
                    máscara para papel nenhum é inelegível a `masked:` numa skill,
                    e o portão de deploy (T5) recusa. Sem este aviso, esvaziar o
                    `by_role` aqui faz o erro aparecer noutro lugar, apontando para
                    o YAML em vez da edição que o causou.
                  */}
                  {!typeMasksSomething(cat) && (
                    <div style={{
                      marginTop: 10, padding: '8px 12px', background: '#1a1206',
                      border: '1px solid #78350f', borderRadius: 6, fontSize: 11,
                      color: '#fbbf24', display: 'flex', gap: 8, alignItems: 'flex-start',
                    }}>
                      <AlertTriangle className="w-4 h-4 flex-shrink-0" aria-hidden="true" />
                      <span>{t('section.typeEditor.inert', {
                        defaultValue: 'This type masks nothing for any role, so it cannot be used in a skill\'s `masked:` declaration — deploy will be refused. Valid for ContextStore map fields that only declare plumbing.',
                      })}</span>
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        </Section>

        {/* ── O MAPA do ContextStore MUDOU DE CASA (ALW-03, 2026-09-02) ─────
            Ele morava aqui desde a CNS-08 e foi para `/config/context-map`, com grant
            proprio (`config.context_map`, preset admin + DEVELOPER).

            O motivo e uma divisao por FATO, decidida pelo dono: "cadastrar um campo"
            sao duas coisas com donos diferentes — o CATALOGO de tipos (o que `cpf_br`
            mascara, sua classe LGPD) e politica de compliance e fica AQUI; o MAPA
            (quais campos existem, qual tipo cada um usa) e AUTORIA e cresce a cada flow.

            Enquanto os dois dividiam `config.masking` (preset admin-only), o cadastro
            ficava fora do alcance de quem autora — `skill_flows.operacao`/`editar`
            nascem para admin+developer. O ADR nomeia essa friccao como o que faz gente
            CONTORNAR o cadastro.                                                   */}

        {/* ── Section 6: Context Store field-level masking rules ─────────── */}
        <Section
          icon={Archive}
          title="Regras de Context Store"
          desc="Controla como cada tag do ContextStore é exibida por role (operator / supervisor). Aplicado em /api/supervisor_state e no painel de contexto do Console. Regras com padrão mais específico têm prioridade."
        >
          <ContextRulesSection
            config={contextRulesConfig}
            saving={saving === 'context_rules'}
            onSave={saveContextRules}
          />
        </Section>
      </div>
    </div>
  )
}

// ── Sub-components ─────────────────────────────────────────────────────────────

function Section({ icon: SectionIcon, title, desc, children }: {
  icon: React.ElementType; title: string; desc: string; children: React.ReactNode
}) {
  return (
    <div style={{ background: '#0d1f38', border: '1px solid #1e293b', borderRadius: 10, padding: '18px 20px' }}>
      <div style={{ display: 'flex', alignItems: 'flex-start', gap: 12, marginBottom: 6 }}>
        <SectionIcon className="w-5 h-5 flex-shrink-0" style={{ color: '#94a3b8', marginTop: 2 } as React.CSSProperties} aria-hidden="true" />
        <div>
          <div style={{ fontWeight: 700, fontSize: 14, color: '#e2e8f0' }}>{title}</div>
          <div style={{ fontSize: 12, color: '#64748b', marginTop: 3, lineHeight: 1.5 }}>{desc}</div>
        </div>
      </div>
      {children}
    </div>
  )
}

/**
 * ProvenanceBanner — ALW-06 / CNS-14 (2026-09-02)
 *
 * ── O que ele responde, e por que a pergunta não era óbvia ──────────────────
 *
 * *"O que eu estou editando: o default da PLATAFORMA ou o override deste tenant?"*
 * A tela nunca disse, e a resposta não é uniforme nem dentro de um namespace: hoje
 * `masking.types` e `masking.context_rules` são respondidos pelo TENANT, enquanto
 * `masking.context_map` é respondido pelo GLOBAL — e a Config API recusa com 422
 * qualquer tentativa de sobrescrever este último por tenant (CNS-08).
 *
 * O dano não é o `PUT` perdido; é a DERIVA. Como a resolução é `LIMIT 1` com o
 * tenant na frente, o override substitui o global POR INTEIRO: depois que ele
 * existe, nenhuma correção de plataforma alcança aquele tenant, e nada fica
 * vermelho. Medido: uma regra de máscara corrigida no global (`*.resume_token`)
 * nunca chegou ao `tenant_demo`; custou um rótulo velho, e da próxima vez custa
 * uma regra.
 *
 * ⚠️ Ele NÃO bloqueia salvar. Tenant com config própria é uso legítimo; o que não
 * pode é ser mudo. O banner nomeia, e quem decide é gente — mesmo desenho que o
 * `config_drift` do seed adotou para a divergência declarado × gravado.
 */
function ProvenanceBanner({ tenantId, groups }: {
  tenantId: string
  groups: Array<{
    ns:      string
    keys:    Record<string, KeyProvenance>
    loading: boolean
    error:   string | null
  }>
}) {
  const { t } = useTranslation('masking')

  const erros = groups.filter(g => g.error)
  const carregando = groups.some(g => g.loading)
  // "sombreada" = tem linha própria do tenant, logo o global não a alcança mais.
  const sombreadas = groups.flatMap(g =>
    Object.entries(g.keys)
      .filter(([, p]) => p.tenant_present && p.global_present)
      .map(([k, p]) => ({ id: `${g.ns}.${k}`, diverges: p.diverges })),
  )

  // Degradação BARULHENTA: sem o relatório a tela volta a não saber o escopo, e
  // esconder isso seria trocar um silêncio por outro.
  if (erros.length > 0) {
    return (
      <div style={{ ...infoBox('#7f1d1d22', '#fca5a5'), display: 'flex', alignItems: 'center', gap: 8 }}>
        <AlertTriangle size={14} aria-hidden="true" />
        {t('provenance.unavailable', {
          defaultValue: 'Could not determine which scope is in effect ({{err}}). Saving still works, but this screen cannot tell you whether your edit reaches the platform default or only this tenant.',
          err: erros.map(e => e.error).join('; '),
        })}
      </div>
    )
  }
  if (carregando) return null

  return (
    <div style={{
      background: sombreadas.length > 0 ? '#78350f22' : '#0f172a',
      border: `1px solid ${sombreadas.length > 0 ? '#b45309' : '#1e293b'}`,
      borderRadius: 8, padding: '12px 16px', fontSize: 12, color: '#cbd5e1',
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 6, fontWeight: 600, color: '#e2e8f0' }}>
        <Lock size={13} aria-hidden="true" />
        {t('provenance.title', { defaultValue: 'Scope in effect' })}
      </div>

      <p style={{ margin: '6px 0 0' }}>
        {t('provenance.writesToTenant', {
          defaultValue: 'Edits on this screen are saved as an override for the tenant "{{tenant}}", never as the platform default.',
          tenant: tenantId,
        })}
      </p>

      {sombreadas.length === 0 ? (
        <p style={{ margin: '6px 0 0', color: '#94a3b8' }}>
          {t('provenance.noneShadowed', {
            defaultValue: 'No key is overridden yet — every value shown comes from the platform default. The first save on a key creates an override, and from then on platform updates to that key no longer reach this tenant.',
          })}
        </p>
      ) : (
        <>
          <p style={{ margin: '6px 0 4px', color: '#fbbf24' }}>
            {t('provenance.shadowedIntro', {
              count: sombreadas.length,
              defaultValue: 'These keys already have a tenant override, so platform updates to them no longer reach this tenant:',
            })}
          </p>
          <ul style={{ margin: 0, paddingLeft: 18 }}>
            {sombreadas.map(s => (
              <li key={s.id} style={{ margin: '2px 0' }}>
                <code style={{ color: '#e2e8f0', background: '#1e293b', padding: '0 4px', borderRadius: 3 }}>{s.id}</code>
                {' — '}
                {s.diverges
                  ? t('provenance.diverges', { defaultValue: 'differs from the platform default' })
                  : t('provenance.identical', { defaultValue: 'identical to the platform default (the override adds nothing and only blocks future updates)' })}
              </li>
            ))}
          </ul>
        </>
      )}
    </div>
  )
}

/** Rotulo das colunas do editor de tipo — extraido porque os tres eixos o repetiam. */
const labelStyle: React.CSSProperties = {
  fontSize: 10, color: '#64748b', fontWeight: 600,
  textTransform: 'uppercase', letterSpacing: '0.06em',
}

/**
 * EchoSelect — o dominio de ECO, igual nas duas pontas (ALW-10, 2026-09-02).
 *
 * Tres valores e nenhum parcial, e a ausencia do parcial e ESTRUTURAL: eco devolve
 * a entrada FRESCA do cliente, nao renderiza um token. Nao ha `***-00` embutido
 * para ler, e fabricar um exigiria re-mascarar na borda — inventando uma terceira
 * resposta para "quanto do valor aparece", ao lado de `by_role` (9 valores) e
 * `token_display` (3).
 *
 * O dominio e CHANNEL-ABSTRACT de proposito: cada adapter traduz conforme o canal
 * (em voz, `masked` e bipe ou placeholder falado; em texto, `••••••`). E o mesmo
 * invariante que o CLAUDE.md ja enuncia para renderizacao por canal.
 */
function EchoSelect({ value, onChange, disabled }: {
  value: EchoMode; onChange: (v: EchoMode) => void; disabled: boolean
}) {
  const { t } = useTranslation('masking')
  return (
    <select
      value={value}
      disabled={disabled}
      onChange={e => onChange(e.target.value as EchoMode)}
      style={{ ...selectStyle }}
    >
      <option value="none">{t('echoMode.none', { defaultValue: 'No echo' })}</option>
      <option value="masked">{t('echoMode.masked', { defaultValue: 'Masked (••••••)' })}</option>
      <option value="plain">{t('echoMode.plain', { defaultValue: 'Plain value' })}</option>
    </select>
  )
}

function ToggleCard({ label, sublabel, active, onToggle, saving, warning }: {
  label: string; sublabel: string; active: boolean
  onToggle: () => void; saving: boolean; warning?: string
}) {
  return (
    <div style={{
      flex: '1 1 220px', background: '#0f172a', borderRadius: 8,
      border: `1px solid ${active ? '#3b82f6' : '#1e293b'}`, padding: '14px 16px',
    }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <div>
          <div style={{ fontWeight: 600, fontSize: 13, color: '#e2e8f0' }}>{label}</div>
          <code style={{ fontSize: 10, color: '#475569' }}>{sublabel}</code>
        </div>
        <button
          onClick={onToggle}
          disabled={saving}
          style={{
            width: 48, height: 26, borderRadius: 13, border: 'none', cursor: 'pointer',
            background: active ? '#3b82f6' : '#1e293b', position: 'relative', transition: 'background 0.2s',
          }}
        >
          <span style={{
            position: 'absolute', top: 3, left: active ? 24 : 3,
            width: 20, height: 20, borderRadius: '50%',
            background: active ? '#fff' : '#64748b', transition: 'left 0.2s',
          }} />
        </button>
      </div>
      {active && warning && (
        <div style={{ marginTop: 8, fontSize: 11, color: '#fbbf24', display: 'flex', alignItems: 'center', gap: 4 }}><AlertTriangle size={12} aria-hidden="true" />{warning}</div>
      )}
    </div>
  )
}

function RetentionEditor({ value, onSave, saving }: {
  value: number; onSave: (v: number) => void; saving: boolean
}) {
  const { t } = useTranslation('masking')
  const [editing, setEditing] = useState(false)
  const [draft,   setDraft]   = useState(String(value))

  function commit() {
    const n = parseInt(draft, 10)
    if (!isNaN(n) && n >= 1 && n <= 365) {
      onSave(n)
      setEditing(false)
    }
  }

  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
      {editing ? (
        <>
          <input
            type="number" min={1} max={365}
            value={draft}
            onChange={e => setDraft(e.target.value)}
            style={{ ...inputStyle, width: 80 }}
            onKeyDown={e => e.key === 'Enter' && commit()}
            autoFocus
          />
          <button onClick={commit} disabled={saving} style={saveBtnStyle}>
            {saving ? '…' : t('retention.save')}
          </button>
          <button onClick={() => setEditing(false)} style={cancelBtnStyle}>{t('retention.cancel')}</button>
        </>
      ) : (
        <>
          <div style={{
            fontSize: 28, fontWeight: 700, color: '#7dd3fc',
            lineHeight: 1, fontVariantNumeric: 'tabular-nums',
          }}>
            {value}
          </div>
          <div style={{ color: '#64748b', fontSize: 13 }}>{t('retention.days')}</div>
          <button onClick={() => { setDraft(String(value)); setEditing(true) }} style={editBtnStyle}>
            <Pencil size={13} style={{ display: 'inline', marginRight: 4 }} aria-hidden="true" />{t('retention.edit')}
          </button>
        </>
      )}
    </div>
  )
}

// ── Styles ─────────────────────────────────────────────────────────────────────

const inputStyle: React.CSSProperties = {
  background: '#0f172a', border: '1px solid #334155', borderRadius: 6,
  color: '#e2e8f0', fontSize: 12, padding: '4px 10px', outline: 'none',
}

const saveBtnStyle: React.CSSProperties = {
  padding: '5px 14px', fontSize: 12, fontWeight: 600, borderRadius: 6,
  background: '#3b82f6', color: '#fff', border: 'none', cursor: 'pointer',
}

const cancelBtnStyle: React.CSSProperties = {
  padding: '5px 14px', fontSize: 12, borderRadius: 6,
  background: 'none', color: '#64748b', border: '1px solid #334155', cursor: 'pointer',
}

const editBtnStyle: React.CSSProperties = {
  padding: '4px 12px', fontSize: 12, borderRadius: 6,
  background: 'none', color: '#64748b', border: '1px solid #334155', cursor: 'pointer',
}

// ⚠️ `MiniToggle` saiu com a ALW-10 (2026-09-02). Servia só aos dois interruptores
// de eco, que eram BOOLEANOS; o domínio virou ternário e o controle virou
// `EchoSelect`. Componente sem chamador é a mesma família da tool que a ALW-07
// removeu no mesmo dia — some junto em vez de esperar alguém reencontrá-lo.

const selectStyle: React.CSSProperties = {
  background: '#0a1628', border: '1px solid #334155', borderRadius: 6,
  color: '#e2e8f0', fontSize: 12, padding: '5px 8px', outline: 'none', cursor: 'pointer',
}

function infoBox(bg: string, color: string): React.CSSProperties {
  return {
    margin: '8px 28px', padding: '10px 16px', background: bg,
    borderRadius: 8, fontSize: 13, color,
  }
}

// ── ContextRulesSection ────────────────────────────────────────────────────────

const EMPTY_RULE: ContextMaskingRule = { pattern: '', role: 'operator', type: 'plain', label: '' }
const ALL_TYPES = Object.keys(MASKING_TYPE_INFO) as ContextMaskingType[]
const ROLE_OPTIONS: Array<ContextMaskingRule['role']> = ['operator', 'supervisor', '*']

function ContextRulesSection({
  config,
  saving,
  onSave,
}: {
  config:  ContextMaskingConfig
  saving:  boolean
  onSave:  (config: ContextMaskingConfig) => void
}) {
  const [rules,       setRules]       = useState<ContextMaskingRule[]>(config.rules)
  const [defaultType, setDefaultType] = useState<ContextMaskingType>(config.default_unmatched_operator)
  const [supRoles,    setSupRoles]    = useState<string>((config.supervisor_roles ?? DEFAULT_SUPERVISOR_ROLES).join(', '))
  const [editIndex,   setEditIndex]   = useState<number | null>(null)
  const [editDraft,   setEditDraft]   = useState<ContextMaskingRule>(EMPTY_RULE)
  const [newDraft,    setNewDraft]    = useState<ContextMaskingRule>(EMPTY_RULE)
  const [showAddRow,  setShowAddRow]  = useState(false)

  // Sync when parent config changes (e.g. after reload)
  React.useEffect(() => {
    setRules(config.rules)
    setDefaultType(config.default_unmatched_operator)
    setSupRoles((config.supervisor_roles ?? DEFAULT_SUPERVISOR_ROLES).join(', '))
  }, [config])

  const supRolesArr = supRoles.split(',').map(s => s.trim()).filter(Boolean)

  function startEdit(index: number) {
    setEditIndex(index)
    setEditDraft({ ...rules[index]! })
  }

  function cancelEdit() {
    setEditIndex(null)
  }

  function commitEdit() {
    if (!editDraft.pattern.trim()) return
    const next = rules.map((r, i) => i === editIndex ? { ...editDraft, pattern: editDraft.pattern.trim() } : r)
    setRules(next)
    setEditIndex(null)
  }

  function deleteRule(index: number) {
    setRules(rules.filter((_, i) => i !== index))
    if (editIndex === index) setEditIndex(null)
  }

  function commitAdd() {
    if (!newDraft.pattern.trim()) return
    setRules([...rules, { ...newDraft, pattern: newDraft.pattern.trim() }])
    setNewDraft(EMPTY_RULE)
    setShowAddRow(false)
  }

  function handleSave() {
    onSave({ rules, default_unmatched_operator: defaultType, supervisor_roles: supRolesArr })
  }

  const hasChanges =
    defaultType !== config.default_unmatched_operator ||
    JSON.stringify(rules) !== JSON.stringify(config.rules) ||
    JSON.stringify(supRolesArr) !== JSON.stringify(config.supervisor_roles ?? DEFAULT_SUPERVISOR_ROLES)

  return (
    <div style={{ marginTop: 16 }}>
      {/* Rules table */}
      <div style={{ overflowX: 'auto', marginBottom: 14 }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
          <thead>
            <tr style={{ borderBottom: '1px solid #1e293b' }}>
              {['Padrão (tag)', 'Role', 'Tipo de máscara', 'Prévia', 'Label', ''].map(h => (
                <th key={h} style={{ padding: '6px 10px', textAlign: 'left', color: '#64748b', fontWeight: 600, whiteSpace: 'nowrap', fontSize: 11 }}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rules.length === 0 && (
              <tr>
                <td colSpan={6} style={{ padding: '16px 10px', color: '#475569', textAlign: 'center', fontStyle: 'italic' }}>
                  Nenhuma regra configurada — usando defaults do sistema
                </td>
              </tr>
            )}
            {rules.map((rule, i) => (
              editIndex === i ? (
                <tr key={i} style={{ background: '#0f1f3a', borderBottom: '1px solid #1e3a5f' }}>
                  <td style={{ padding: '6px 8px' }}>
                    <input
                      value={editDraft.pattern}
                      onChange={e => setEditDraft(d => ({ ...d, pattern: e.target.value }))}
                      placeholder="caller.cpf ou caller.*"
                      style={{ ...inputStyle, width: 160 }}
                    />
                  </td>
                  <td style={{ padding: '6px 8px' }}>
                    <select value={editDraft.role} onChange={e => setEditDraft(d => ({ ...d, role: e.target.value as ContextMaskingRule['role'] }))} style={selectStyle}>
                      {ROLE_OPTIONS.map(r => <option key={r} value={r}>{r}</option>)}
                    </select>
                  </td>
                  <td style={{ padding: '6px 8px' }}>
                    <select value={editDraft.type} onChange={e => setEditDraft(d => ({ ...d, type: e.target.value as ContextMaskingType }))} style={selectStyle}>
                      {ALL_TYPES.map(t => <option key={t} value={t}>{MASKING_TYPE_INFO[t].label}</option>)}
                    </select>
                  </td>
                  <td style={{ padding: '6px 8px' }}>
                    <code style={{ color: '#7dd3fc', fontSize: 11 }}>{MASKING_TYPE_INFO[editDraft.type].sample}</code>
                  </td>
                  <td style={{ padding: '6px 8px' }}>
                    <input
                      value={editDraft.label ?? ''}
                      onChange={e => setEditDraft(d => ({ ...d, label: e.target.value }))}
                      placeholder="Descrição opcional"
                      style={{ ...inputStyle, width: 140 }}
                    />
                  </td>
                  <td style={{ padding: '6px 8px', whiteSpace: 'nowrap' }}>
                    <button onClick={commitEdit} style={{ ...saveBtnStyle, fontSize: 11, padding: '3px 10px', marginRight: 6 }} aria-label="Salvar"><Check size={12} aria-hidden="true" /></button>
                    <button onClick={cancelEdit} style={{ ...cancelBtnStyle, fontSize: 11, padding: '3px 10px' }} aria-label="Cancelar"><X size={12} aria-hidden="true" /></button>
                  </td>
                </tr>
              ) : (
                <tr key={i} style={{ borderBottom: '1px solid #1a2640', transition: 'background 0.1s' }}
                  onMouseEnter={e => (e.currentTarget.style.background = '#0f1a2e')}
                  onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}>
                  <td style={{ padding: '7px 10px' }}>
                    <code style={{ color: '#7dd3fc', fontSize: 12, background: '#0c1a30', padding: '1px 5px', borderRadius: 3 }}>{rule.pattern}</code>
                  </td>
                  <td style={{ padding: '7px 10px' }}>
                    <span style={{
                      fontSize: 11, fontWeight: 600,
                      color: rule.role === 'supervisor' ? '#a78bfa' : rule.role === '*' ? '#94a3b8' : '#60a5fa',
                    }}>{rule.role}</span>
                  </td>
                  <td style={{ padding: '7px 10px', color: '#cbd5e1' }}>{MASKING_TYPE_INFO[rule.type].label}</td>
                  <td style={{ padding: '7px 10px' }}>
                    <code style={{ color: '#f59e0b', fontSize: 11 }}>{MASKING_TYPE_INFO[rule.type].sample}</code>
                  </td>
                  <td style={{ padding: '7px 10px', color: '#64748b' }}>{rule.label ?? '—'}</td>
                  <td style={{ padding: '7px 10px', whiteSpace: 'nowrap' }}>
                    <button onClick={() => startEdit(i)} style={{ ...editBtnStyle, fontSize: 11, padding: '2px 8px', marginRight: 6 }} aria-label="Editar"><Pencil size={11} aria-hidden="true" /></button>
                    <button onClick={() => deleteRule(i)} style={{ ...cancelBtnStyle, fontSize: 11, padding: '2px 8px', color: '#ef4444', borderColor: '#ef444444' }} aria-label="Excluir"><X size={11} aria-hidden="true" /></button>
                  </td>
                </tr>
              )
            ))}

            {/* Add-new row */}
            {showAddRow && (
              <tr style={{ background: '#0b1e38', borderBottom: '1px solid #1e3a5f' }}>
                <td style={{ padding: '6px 8px' }}>
                  <input
                    value={newDraft.pattern}
                    onChange={e => setNewDraft(d => ({ ...d, pattern: e.target.value }))}
                    placeholder="caller.cpf ou caller.*"
                    style={{ ...inputStyle, width: 160 }}
                    autoFocus
                  />
                </td>
                <td style={{ padding: '6px 8px' }}>
                  <select value={newDraft.role} onChange={e => setNewDraft(d => ({ ...d, role: e.target.value as ContextMaskingRule['role'] }))} style={selectStyle}>
                    {ROLE_OPTIONS.map(r => <option key={r} value={r}>{r}</option>)}
                  </select>
                </td>
                <td style={{ padding: '6px 8px' }}>
                  <select value={newDraft.type} onChange={e => setNewDraft(d => ({ ...d, type: e.target.value as ContextMaskingType }))} style={selectStyle}>
                    {ALL_TYPES.map(t => <option key={t} value={t}>{MASKING_TYPE_INFO[t].label}</option>)}
                  </select>
                </td>
                <td style={{ padding: '6px 8px' }}>
                  <code style={{ color: '#7dd3fc', fontSize: 11 }}>{MASKING_TYPE_INFO[newDraft.type].sample}</code>
                </td>
                <td style={{ padding: '6px 8px' }}>
                  <input
                    value={newDraft.label ?? ''}
                    onChange={e => setNewDraft(d => ({ ...d, label: e.target.value }))}
                    placeholder="Descrição opcional"
                    style={{ ...inputStyle, width: 140 }}
                  />
                </td>
                <td style={{ padding: '6px 8px', whiteSpace: 'nowrap' }}>
                  <button onClick={commitAdd} style={{ ...saveBtnStyle, fontSize: 11, padding: '3px 10px', marginRight: 6 }}>+ Adicionar</button>
                  <button onClick={() => { setShowAddRow(false); setNewDraft(EMPTY_RULE) }} style={{ ...cancelBtnStyle, fontSize: 11, padding: '3px 10px' }} aria-label="Cancelar"><X size={12} aria-hidden="true" /></button>
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {/* Add row button */}
      {!showAddRow && (
        <button
          onClick={() => setShowAddRow(true)}
          style={{ ...editBtnStyle, fontSize: 12, marginBottom: 16 }}
        >
          + Nova regra
        </button>
      )}

      {/* Default unmatched operator */}
      <div style={{
        display: 'flex', alignItems: 'center', gap: 12, marginBottom: 16,
        padding: '12px 14px', background: '#0f172a', borderRadius: 8, border: '1px solid #1e293b',
      }}>
        <div style={{ flex: 1 }}>
          <div style={{ fontSize: 12, fontWeight: 600, color: '#e2e8f0' }}>Padrão para operator sem regra</div>
          <div style={{ fontSize: 11, color: '#64748b', marginTop: 2 }}>
            Aplicado quando nenhuma regra casa com a tag para o role <code style={{ color: '#94a3b8' }}>operator</code>.
            Supervisors sem regra sempre veem <code style={{ color: '#94a3b8' }}>plain</code>.
          </div>
        </div>
        <select
          value={defaultType}
          onChange={e => setDefaultType(e.target.value as ContextMaskingType)}
          style={{ ...selectStyle, minWidth: 180 }}
        >
          {ALL_TYPES.map(t => (
            <option key={t} value={t}>{MASKING_TYPE_INFO[t].label} — {MASKING_TYPE_INFO[t].sample}</option>
          ))}
        </select>
      </div>

      {/* Supervisor roles — who bypasses the namespace gate and sees PII plain */}
      <div style={{
        display: 'flex', alignItems: 'center', gap: 12, marginBottom: 16,
        padding: '12px 14px', background: '#0f172a', borderRadius: 8, border: '1px solid #1e293b',
      }}>
        <div style={{ flex: 1 }}>
          <div style={{ fontSize: 12, fontWeight: 600, color: '#e2e8f0' }}>Roles tratados como supervisor</div>
          <div style={{ fontSize: 11, color: '#64748b', marginTop: 2 }}>
            Estes roles ignoram a namespace gate e veem os campos <code style={{ color: '#94a3b8' }}>plain</code>.
            Qualquer role fora da lista é tratado como <code style={{ color: '#94a3b8' }}>operator</code>. Separe por vírgula.
          </div>
        </div>
        <input
          type="text"
          value={supRoles}
          onChange={e => setSupRoles(e.target.value)}
          placeholder="supervisor, admin, evaluator, reviewer"
          style={{ ...selectStyle, minWidth: 260 }}
        />
      </div>

      {/* Save */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
        <button
          onClick={handleSave}
          disabled={saving || !hasChanges}
          style={{
            ...saveBtnStyle,
            opacity: saving || !hasChanges ? 0.5 : 1,
            cursor: saving || !hasChanges ? 'not-allowed' : 'pointer',
          }}
        >
          {saving ? 'Salvando…' : 'Salvar Regras'}
        </button>
        {hasChanges && (
          <span style={{ fontSize: 11, color: '#fbbf24', display: 'inline-flex', alignItems: 'center', gap: 4 }}><AlertTriangle size={12} aria-hidden="true" />Alterações não salvas</span>
        )}
        {!hasChanges && rules.length > 0 && (
          <span style={{ fontSize: 11, color: '#22c55e', display: 'inline-flex', alignItems: 'center', gap: 4 }}><Check size={12} aria-hidden="true" />Sincronizado</span>
        )}
        <span style={{ fontSize: 11, color: '#475569', marginLeft: 'auto' }}>
          {rules.length} {rules.length === 1 ? 'regra' : 'regras'} configuradas
        </span>
      </div>

      {/* Hint about specificity */}
      <div style={{ marginTop: 12, padding: '8px 12px', background: '#0c1520', borderRadius: 6, border: '1px solid #1e293b' }}>
        <p style={{ margin: 0, fontSize: 11, color: '#475569', lineHeight: 1.6 }}>
          <strong style={{ color: '#64748b' }}>Prioridade de regras:</strong>{' '}
          padrão exato (<code style={{ color: '#7dd3fc' }}>caller.cpf</code>) &gt; glob de namespace (<code style={{ color: '#7dd3fc' }}>caller.*</code>) &gt; curinga (<code style={{ color: '#7dd3fc' }}>*</code>).
          Role exato supera <code style={{ color: '#7dd3fc' }}>*</code>. Role <code style={{ color: '#a78bfa' }}>supervisor</code> cobre supervisor, admin, evaluator e reviewer.
        </p>
      </div>
    </div>
  )
}
