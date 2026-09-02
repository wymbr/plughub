/**
 * ContextMapPage — /config/context-map
 *
 * O CADASTRO de campos do ContextStore: quais existem, em `escopo.dominio.campo`, e
 * qual TIPO cada um usa. O mapa é a allowlist (D9 do arco ALLOWLIST).
 *
 * ── Por que esta tela existe separada da de Mascaramento (ALW-03, 2026-09-02) ──
 *
 * A seção do mapa nasceu DENTRO da MaskingPage (CNS-08) e saiu de lá por decisão do
 * dono, porque *"cadastrar um campo"* são **dois fatos com donos diferentes**:
 *
 *   · o **CATÁLOGO de tipos** — o que `cpf_br` mascara, para quais papéis, e sua classe
 *     LGPD. É política de compliance, e fica em `/config/masking` (`config.masking`).
 *   · o **MAPA** — quais campos existem e qual tipo cada um usa. Isso é AUTORIA: cresce a
 *     cada flow novo, e quem sabe o que o campo guarda é quem escreveu o flow.
 *
 * Enquanto os dois viviam no mesmo grant, o cadastro ficava fora do alcance de quem
 * autora. Medido em 2026-09-02: `skill_flows.operacao` e `skill_flows.editar` nascem para
 * **admin + developer**; `config.masking` nasce **só para admin**. O ADR nomeia essa
 * fricção como exatamente o que faz gente CONTORNAR o cadastro — e um cadastro
 * contornado é a §1.1 de volta (*o valor visível porque ninguém decidiu*).
 *
 * Hoje o grant é `config.context_map`, com o `developer` no preset.
 *
 * ── O que o autor PODE e o que ele NÃO pode ───────────────────────────────────
 *
 * Pode dizer que `session.cartao.numero` é um `numero_cartao`. **Não** pode criar tipo
 * novo nem mudar o que um tipo significa — o `<select>` só oferece o catálogo, que é
 * carregado em leitura. Escolher `texto` para um campo sensível é possível, e isso é
 * decisão registrada do ADR, não descuido: *"garantir que a decisão seja BOA nunca foi o
 * contrato"* — o contrato é que ALGUÉM decidiu, explicitamente e de forma auditável.
 *
 * ⚠️ **Grava no ESCOPO GLOBAL** (`tenantId` = null), e isso é medição. A resolução de
 * config é tenant-vence-global POR INTEIRO (`LIMIT 1`), então override de tenant nesta
 * chave substituiria as folhas da plataforma em vez de acrescentar às delas — medido: um
 * `PUT` com uma folha deixou o tenant com 1 no lugar de 94. O config-api RECUSA (422);
 * aqui a tela nem oferece o caminho. Vocabulário por tenant é desenho registrado (chave
 * separada + merge), adiado por população zero — ver CNS-16.
 */
import React, { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Archive } from 'lucide-react'
import { useAuth } from '@/auth/useAuth'
import { useNamespace, putConfig } from '../config-plataforma/api/config-hooks'

/** Root que só a plataforma declara — mostrado em SOMENTE LEITURA. Editar uma folha do
 *  core aqui criaria drift contra o `seed.py`, que é o dono dela, e o gate
 *  `probe_seed_drift_named` passaria a acusar uma divergência que a tela criou. */
const PLATFORM_ROOT = 'core'

interface ContextMapLeaf { tipo: string; legado?: string[]; label?: string }
interface ContextMapDoc {
  mode?:             string
  dynamic_prefixes?: string[]
  contexto:          Record<string, Record<string, Record<string, ContextMapLeaf>>>
}
interface DataTypeEntry { id: string; label?: string }
interface DataTypeCatalog { types: DataTypeEntry[] }

export default function ContextMapPage() {
  const { t } = useTranslation('masking')
  const { tenantId, session } = useAuth()
  const bearer = session?.accessToken ?? ''
  const [saving, setSaving] = useState(false)
  const [toast,  setToast]  = useState<{ msg: string; ok: boolean } | null>(null)

  const { entries, loading, error, reload } = useNamespace(tenantId, 'masking')

  const rawCatalog = entries['types']?.value ?? entries['types']
  const catalog: DataTypeCatalog = (rawCatalog && typeof rawCatalog === 'object')
    ? (rawCatalog as DataTypeCatalog)
    : { types: [] }
  const dataTypes = catalog.types ?? []

  const rawMap = entries['context_map']?.value ?? entries['context_map']
  const contextMap: ContextMapDoc = (rawMap && typeof rawMap === 'object')
    ? (rawMap as ContextMapDoc)
    : { contexto: {} }

  function showToast(msg: string, ok: boolean) {
    setToast({ msg, ok })
    setTimeout(() => setToast(null), 4000)
  }

  async function saveContextMap(next: ContextMapDoc) {
    if (!bearer) { showToast(t('toast.tokenRequired'), false); return }
    setSaving(true)
    try {
      await putConfig('masking', 'context_map', next, null, '', bearer)
      reload()
      showToast(t('toast.keySaved', { key: 'context_map' }), true)
    } catch (e) {
      showToast(String(e), false)
    } finally {
      setSaving(false)
    }
  }

  /** Muda o tipo de UMA folha, preservando `legado`/`label` — o resto do nó é dado que a
   *  tela não autora e não pode perder num round-trip de edição. */
  function setLeafTipo(root: string, dom: string, campo: string, tipo: string) {
    const c = contextMap.contexto ?? {}
    saveContextMap({
      ...contextMap,
      contexto: {
        ...c,
        [root]: { ...c[root], [dom]: { ...c[root]?.[dom], [campo]: { ...c[root]?.[dom]?.[campo], tipo } } },
      },
    })
  }

  const roots = Object.entries(contextMap.contexto ?? {}).sort(([a], [b]) => a.localeCompare(b))

  return (
    <div style={{ padding: '24px 28px', maxWidth: 1200 }}>
      <div style={{ marginBottom: 20 }}>
        <h1 style={{ fontSize: 20, fontWeight: 700, color: '#e2e8f0', margin: 0 }}>
          {t('contextMapPage.title', { defaultValue: 'ContextStore fields' })}
        </h1>
        <p style={{ fontSize: 13, color: '#64748b', marginTop: 6, lineHeight: 1.6, maxWidth: 760 }}>
          {t('contextMapPage.subtitle', { defaultValue: 'Which fields exist in the ContextStore, in escopo.dominio.campo, each naming a type from the catalogue. The map is the allowlist: a field that is not here is not declared. The catalogue itself — what each type masks, and its LGPD class — is configured under Masking.' })}
        </p>
      </div>

      {loading && <div style={{ color: '#64748b', fontSize: 13 }}>{t('loading', { defaultValue: 'Loading…' })}</div>}
      {error   && <div style={{ color: '#f87171', fontSize: 13 }}>{String(error)}</div>}

      <div style={{ background: '#0d1f38', border: '1px solid #1e293b', borderRadius: 10, padding: '18px 20px' }}>
        <div style={{ display: 'flex', alignItems: 'flex-start', gap: 12, marginBottom: 6 }}>
          <Archive className="w-5 h-5 flex-shrink-0" style={{ color: '#94a3b8', marginTop: 2 } as React.CSSProperties} aria-hidden="true" />
          <div>
            <div style={{ fontWeight: 700, fontSize: 14, color: '#e2e8f0' }}>
              {t('section.contextMap.title', { defaultValue: 'ContextStore map — declared fields' })}
            </div>
            <div style={{ fontSize: 12, color: '#64748b', marginTop: 3, lineHeight: 1.5 }}>
              {t('section.contextMap.description', { defaultValue: 'Which fields exist in the ContextStore, in escopo.dominio.campo, each naming its type. The map is the allowlist. Saved to the GLOBAL scope.' })}
            </div>
          </div>
        </div>

        {!loading && roots.length === 0 && (
          <div style={{ marginTop: 12, padding: '10px 14px', background: '#1a1206', border: '1px solid #78350f', borderRadius: 8, fontSize: 12, color: '#fbbf24' }}>
            {t('section.contextMap.absent', { defaultValue: 'masking.context_map is not set — the platform seeds it at boot.' })}
          </div>
        )}

        {roots.map(([root, doms]) => {
          const daPlataforma = root === PLATFORM_ROOT
          const nFolhas = Object.values(doms).reduce((n, c) => n + Object.keys(c).length, 0)
          return (
            <div key={root} style={{ marginTop: 14, background: '#0f172a', border: '1px solid #1e293b', borderRadius: 8, padding: '12px 16px' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 10 }}>
                <code style={{ fontSize: 13, fontWeight: 700, color: daPlataforma ? '#7dd3fc' : '#e2e8f0' }}>{root}.*</code>
                <span style={{ fontSize: 11, color: '#64748b' }}>
                  {nFolhas} {t('section.contextMap.leaves', { defaultValue: 'fields' })}
                </span>
                {daPlataforma && (
                  <span style={{
                    display: 'inline-block', padding: '2px 8px', borderRadius: 12, fontSize: 11,
                    fontWeight: 600, background: '#7dd3fc22', color: '#7dd3fc', border: '1px solid #7dd3fc44',
                  }}>{t('section.contextMap.platform', { defaultValue: 'platform — read only' })}</span>
                )}
              </div>
              {daPlataforma && (
                <p style={{ margin: '0 0 10px', fontSize: 11, color: '#64748b', lineHeight: 1.6 }}>
                  {t('section.contextMap.platformNote', { defaultValue: 'Declared by the platform seed and reserved: a tenant cannot write here. Change it in seed.py and reapply.' })}
                </p>
              )}
              {Object.entries(doms).sort(([a], [b]) => a.localeCompare(b)).map(([dom, campos]) => (
                <div key={dom} style={{ marginBottom: 8 }}>
                  <div style={{ fontSize: 11, color: '#94a3b8', fontWeight: 600, marginBottom: 4 }}>{dom}</div>
                  <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(320px, 1fr))', gap: 6 }}>
                    {Object.entries(campos).sort(([a], [b]) => a.localeCompare(b)).map(([campo, leaf]) => (
                      <div key={campo} style={{ display: 'flex', alignItems: 'center', gap: 8, background: '#0a1628', borderRadius: 6, padding: '5px 10px' }}>
                        <code style={{ fontSize: 11, color: '#cbd5e1', flex: 1, overflow: 'hidden', textOverflow: 'ellipsis' }}>{campo}</code>
                        {daPlataforma ? (
                          <code style={{ fontSize: 11, color: '#7dd3fc' }}>{leaf.tipo}</code>
                        ) : (
                          <select
                            value={leaf.tipo}
                            disabled={saving}
                            onChange={e => setLeafTipo(root, dom, campo, e.target.value)}
                            style={{
                              background: '#0f172a', border: '1px solid #1e293b', borderRadius: 6,
                              color: '#e2e8f0', fontSize: 11, padding: '3px 6px',
                            }}
                          >
                            {/* Só o CATÁLOGO. O autor escolhe o tipo; não cria tipo. */}
                            {dataTypes.map(dt => <option key={dt.id} value={dt.id}>{dt.id}</option>)}
                          </select>
                        )}
                        {(leaf.legado?.length ?? 0) > 0 && (
                          <span
                            title={leaf.legado?.join(', ')}
                            style={{ fontSize: 10, color: '#64748b', cursor: 'help' }}
                          >{leaf.legado?.length} {t('section.contextMap.aliases', { defaultValue: 'aliases' })}</span>
                        )}
                      </div>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          )
        })}

        <div style={{ marginTop: 12, padding: '10px 14px', background: '#0f172a', borderRadius: 8, border: '1px solid #1e293b' }}>
          <p style={{ margin: 0, fontSize: 12, color: '#64748b', lineHeight: 1.6 }}>
            {t('section.contextMap.scopeNote', { defaultValue: 'Edits are saved to the GLOBAL scope. A per-tenant override would replace the platform declarations wholesale instead of adding to them, so the Config API refuses it (422).' })}
          </p>
          <p style={{ margin: '8px 0 0', fontSize: 12, color: '#64748b', lineHeight: 1.6 }}>
            {t('contextMapPage.catalogueNote', { defaultValue: 'The type list comes from the catalogue and cannot be extended here. What a type masks, for which roles, and its LGPD class are configured under Masking — a separate grant, because they are a separate decision.' })}
          </p>
        </div>
      </div>

      {toast && (
        <div style={{
          position: 'fixed', bottom: 24, right: 24, padding: '12px 18px', borderRadius: 8,
          background: toast.ok ? '#052e1a' : '#2e0505',
          border: `1px solid ${toast.ok ? '#059669' : '#dc2626'}`,
          color: toast.ok ? '#6ee7b7' : '#fca5a5', fontSize: 13, zIndex: 50,
        }}>{toast.msg}</div>
      )}
    </div>
  )
}
