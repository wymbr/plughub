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

/**
 * Escopos que o autor pode criar. **Lista fechada, e isso não é conservadorismo.**
 *
 * O primeiro segmento da tag ROTEIA hash e TTL (`CONTEXT_ROUTE_PREFIXES`, em
 * `@plughub/schemas`): `session` → 4 h, `journey` → 30 d. Um escopo inventado aqui cairia
 * no default sem que nada reclamasse — retenção de PII decidida por digitação, degradando
 * mudo. `core` fica de fora porque é da plataforma, sem exceção.
 */
const ESCOPOS_AUTORAVEIS = ['session', 'journey'] as const

/** `escopo.dominio.campo` — três segmentos, minúsculas, `_` e dígitos. */
const SEG = /^[a-z][a-z0-9_]*$/

function validaNome(escopo: string, dominio: string, campo: string): string | null {
  if (!ESCOPOS_AUTORAVEIS.includes(escopo as typeof ESCOPOS_AUTORAVEIS[number]))
    return `escopo inválido — use ${ESCOPOS_AUTORAVEIS.join(' ou ')}`
  if (!SEG.test(dominio)) return 'domínio: minúsculas, dígitos e _ , começando por letra'
  if (!SEG.test(campo))   return 'campo: minúsculas, dígitos e _ , começando por letra'
  return null
}

interface ContextMapLeaf { tipo: string; legado?: string[]; label?: string; arquivado?: boolean }
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

  // ── E2/E3/E4 — a tela deixou de ser só leitura de tipo ────────────────────
  const [novoCampo, setNovoCampo] = useState({ escopo: 'session', dominio: '', campo: '', tipo: '' })
  const [renomeando, setRenomeando] = useState<{ root: string; dom: string; campo: string } | null>(null)
  const [novoNome, setNovoNome] = useState('')
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

  /** E2 — CRIAR campo. Aditivo: não há dado gravado sob um nome que ainda não existe. */
  function criarCampo() {
    const { escopo, dominio, campo, tipo } = novoCampo
    const erro = validaNome(escopo, dominio, campo)
    if (erro)  { showToast(erro, false); return }
    if (!tipo) { showToast(t('contextMapPage.pickType', { defaultValue: 'Choose a type from the catalogue.' }), false); return }
    const c = contextMap.contexto ?? {}
    if (c[escopo]?.[dominio]?.[campo]) {
      showToast(`${escopo}.${dominio}.${campo} ${t('contextMapPage.exists', { defaultValue: 'already exists' })}`, false)
      return
    }
    saveContextMap({
      ...contextMap,
      contexto: { ...c, [escopo]: { ...c[escopo], [dominio]: { ...c[escopo]?.[dominio], [campo]: { tipo } } } },
    })
    setNovoCampo({ escopo, dominio, campo: '', tipo: '' })
  }

  /**
   * E3 — RENOMEAR, e **o nome velho vira alias automaticamente**.
   *
   * Renomear parece "alterar", mas o valor JÁ GRAVADO não se move junto: ele continua no
   * hash sob a grafia antiga. Sem o alias, tudo que existe passa a resolver como não
   * declarado — o histórico inteiro deixa de ser mascarado, que é o oposto do que o mapa
   * existe para fazer.
   *
   * O array `legado` É o mecanismo de rename; não é resíduo. Por isso quem gera é a tela,
   * e não o autor lembrar. Os 119 aliases que a V5 mediu são renames que já aconteceram.
   */
  function renomearCampo(root: string, dom: string, campoVelho: string, alvo: string) {
    const partes = alvo.split('.')
    if (partes.length !== 3) { showToast(t('contextMapPage.threeSegments', { defaultValue: 'Use escopo.dominio.campo — three segments.' }), false); return }
    const [escopo, dominio, campo] = partes as [string, string, string]
    const erro = validaNome(escopo, dominio, campo)
    if (erro) { showToast(erro, false); return }

    const c = contextMap.contexto ?? {}
    const folha = c[root]?.[dom]?.[campoVelho]
    if (!folha) { showToast('folha não encontrada', false); return }
    if (c[escopo]?.[dominio]?.[campo]) { showToast(`${alvo} já existe`, false); return }

    const nomeVelho = `${root}.${dom}.${campoVelho}`
    const legado = [...new Set([...(folha.legado ?? []), nomeVelho])]

    // Clone profundo e mutação imperativa, de propósito: a versão anterior fazia isto com
    // spreads aninhados e condicionais `root === escopo`, e o caso "renomear dentro do
    // MESMO domínio" dependia de três ternários casarem. Um erro ali apagaria as folhas
    // irmãs em silêncio — o mapa é a allowlist, e perder folha aqui é desmascarar
    // histórico. Legibilidade neste ponto é propriedade de segurança, não estilo.
    const próximo: ContextMapDoc['contexto'] = JSON.parse(JSON.stringify(c))
    delete próximo[root]![dom]![campoVelho]
    if (Object.keys(próximo[root]![dom]!).length === 0) delete próximo[root]![dom]
    if (Object.keys(próximo[root]!).length === 0)       delete próximo[root]
    próximo[escopo]            ??= {}
    próximo[escopo]![dominio]  ??= {}
    próximo[escopo]![dominio]![campo] = { ...folha, legado }

    saveContextMap({ ...contextMap, contexto: próximo })
    setRenomeando(null)
    showToast(t('contextMapPage.renamed', {
      defaultValue: 'Renamed. "{{old}}" kept as alias so already-stored data stays masked.',
      old: nomeVelho,
    }), true)
  }

  /**
   * E4 — ARQUIVAR / desarquivar. **Não apaga**, e a distinção é o ponto:
   *
   *   · W (escrita)  — o portão de publish RECUSA um flow que escreva nela;
   *   · R (leitura)  — a folha fica, com o tipo, e o histórico segue mascarado.
   *
   * Mesma forma que `adr-dialog-form-deletion` escolheu para o DialogForm. Purga real
   * existe só para o que nunca foi usado, e "nunca foi usado" é medível — balde C de
   * `infra/test/aliases_v5_buckets.py` —, então ela NÃO mora nesta tela: aqui não há como
   * saber se algum produtor ainda escreve o campo.
   */
  function alternarArquivo(root: string, dom: string, campo: string, arquivar: boolean) {
    const c = contextMap.contexto ?? {}
    const folha = c[root]?.[dom]?.[campo]
    if (!folha) return
    const nova: ContextMapLeaf = { ...folha }
    if (arquivar) nova.arquivado = true
    else delete nova.arquivado          // ausente = ativo; `false` seria ruído no JSON
    saveContextMap({
      ...contextMap,
      contexto: { ...c, [root]: { ...c[root], [dom]: { ...c[root]?.[dom], [campo]: nova } } },
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
                        <code style={{
                          fontSize: 11, flex: 1, overflow: 'hidden', textOverflow: 'ellipsis',
                          // Arquivado tem de SE ANUNCIAR na lista: um campo que o publish
                          // recusa e que parece igual aos outros é a mesma armadilha do
                          // valor plausível, do lado da tela.
                          color: leaf.arquivado ? '#78716c' : '#cbd5e1',
                          textDecoration: leaf.arquivado ? 'line-through' : 'none',
                        }}>{campo}</code>
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
                        {!daPlataforma && (
                          <>
                            <button
                              title={t('contextMapPage.rename', { defaultValue: 'Rename (keeps an alias)' })}
                              disabled={saving}
                              onClick={() => { setRenomeando({ root, dom, campo }); setNovoNome(`${root}.${dom}.${campo}`) }}
                              style={{ background: 'none', border: 'none', color: '#64748b', fontSize: 11, cursor: 'pointer' }}
                            >✎</button>
                            <button
                              title={leaf.arquivado
                                ? t('contextMapPage.unarchive', { defaultValue: 'Unarchive' })
                                : t('contextMapPage.archive',   { defaultValue: 'Archive — blocks new writes, keeps history masked' })}
                              disabled={saving}
                              onClick={() => alternarArquivo(root, dom, campo, !leaf.arquivado)}
                              style={{ background: 'none', border: 'none', color: leaf.arquivado ? '#fbbf24' : '#64748b', fontSize: 11, cursor: 'pointer' }}
                            >{leaf.arquivado ? '⊘' : '⊗'}</button>
                          </>
                        )}
                      </div>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          )
        })}

        {/* ── E2 — cadastrar campo ──────────────────────────────────────────── */}
        <div style={{ marginTop: 14, padding: '12px 16px', background: '#0f172a', borderRadius: 8, border: '1px solid #1e293b' }}>
          <div style={{ fontWeight: 600, fontSize: 13, color: '#e2e8f0', marginBottom: 8 }}>
            {t('contextMapPage.addField', { defaultValue: 'Register a field' })}
          </div>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, alignItems: 'center' }}>
            {/* Escopo é SELECT e não texto: o primeiro segmento roteia hash e TTL. */}
            <select
              value={novoCampo.escopo}
              disabled={saving}
              onChange={e => setNovoCampo({ ...novoCampo, escopo: e.target.value })}
              style={{         background: '#0f172a', border: '1px solid #1e293b', borderRadius: 6, color: '#e2e8f0', fontSize: 11, padding: '4px 7px' }}
            >
              {ESCOPOS_AUTORAVEIS.map(x => <option key={x} value={x}>{x}</option>)}
            </select>
            <span style={{ color: '#475569' }}>.</span>
            <input
              value={novoCampo.dominio} disabled={saving}
              onChange={e => setNovoCampo({ ...novoCampo, dominio: e.target.value })}
              placeholder={t('contextMapPage.domain', { defaultValue: 'domain' })}
              style={{         background: '#0f172a', border: '1px solid #1e293b', borderRadius: 6, color: '#e2e8f0', fontSize: 11, padding: '4px 7px', width: 130 }}
            />
            <span style={{ color: '#475569' }}>.</span>
            <input
              value={novoCampo.campo} disabled={saving}
              onChange={e => setNovoCampo({ ...novoCampo, campo: e.target.value })}
              placeholder={t('contextMapPage.field', { defaultValue: 'field' })}
              style={{         background: '#0f172a', border: '1px solid #1e293b', borderRadius: 6, color: '#e2e8f0', fontSize: 11, padding: '4px 7px', width: 150 }}
            />
            <select
              value={novoCampo.tipo} disabled={saving}
              onChange={e => setNovoCampo({ ...novoCampo, tipo: e.target.value })}
              style={{         background: '#0f172a', border: '1px solid #1e293b', borderRadius: 6, color: '#e2e8f0', fontSize: 11, padding: '4px 7px' }}
            >
              <option value="">{t('contextMapPage.pickTypeShort', { defaultValue: 'type…' })}</option>
              {dataTypes.map(dt => <option key={dt.id} value={dt.id}>{dt.id}</option>)}
            </select>
            <button
              onClick={criarCampo} disabled={saving}
              style={{ background: '#1d4ed8', border: 'none', borderRadius: 6, color: '#fff', fontSize: 12, padding: '5px 14px', cursor: saving ? 'default' : 'pointer' }}
            >{t('contextMapPage.add', { defaultValue: 'Register' })}</button>
          </div>
          <p style={{ margin: '8px 0 0', fontSize: 11, color: '#64748b', lineHeight: 1.6 }}>
            {t('contextMapPage.addNote', { defaultValue: 'The scope is a closed list because the first segment routes the store and its retention: session = 4h, journey = 30d. Typing a new scope would decide PII retention by accident.' })}
          </p>
        </div>

        <div style={{ marginTop: 12, padding: '10px 14px', background: '#0f172a', borderRadius: 8, border: '1px solid #1e293b' }}>
          <p style={{ margin: 0, fontSize: 12, color: '#64748b', lineHeight: 1.6 }}>
            {t('section.contextMap.scopeNote', { defaultValue: 'Edits are saved to the GLOBAL scope. A per-tenant override would replace the platform declarations wholesale instead of adding to them, so the Config API refuses it (422).' })}
          </p>
          <p style={{ margin: '8px 0 0', fontSize: 12, color: '#64748b', lineHeight: 1.6 }}>
            {t('contextMapPage.catalogueNote', { defaultValue: 'The type list comes from the catalogue and cannot be extended here. What a type masks, for which roles, and its LGPD class are configured under Masking — a separate grant, because they are a separate decision.' })}
          </p>
        </div>
      </div>

      {renomeando && (
        <div style={{ position: 'fixed', inset: 0, background: '#00000099', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 60 }}>
          <div style={{ background: '#0d1f38', border: '1px solid #1e293b', borderRadius: 10, padding: 20, width: 460 }}>
            <div style={{ fontWeight: 700, fontSize: 14, color: '#e2e8f0', marginBottom: 6 }}>
              {t('contextMapPage.rename', { defaultValue: 'Rename (keeps an alias)' })}
            </div>
            <p style={{ margin: '0 0 12px', fontSize: 12, color: '#94a3b8', lineHeight: 1.6 }}>
              {t('contextMapPage.renameNote', { defaultValue: 'Data already stored keeps the OLD name. The old spelling is added to this field aliases automatically, so that history stays masked.' })}
            </p>
            <code style={{ fontSize: 11, color: '#64748b' }}>{renomeando.root}.{renomeando.dom}.{renomeando.campo}</code>
            <input
              value={novoNome} autoFocus
              onChange={e => setNovoNome(e.target.value)}
              style={{ display: 'block', width: '100%', marginTop: 8, background: '#0f172a', border: '1px solid #1e293b', borderRadius: 6, color: '#e2e8f0', fontSize: 12, padding: '7px 9px' }}
            />
            <div style={{ display: 'flex', gap: 8, marginTop: 14, justifyContent: 'flex-end' }}>
              <button onClick={() => setRenomeando(null)}
                style={{ background: 'none', border: '1px solid #1e293b', borderRadius: 6, color: '#94a3b8', fontSize: 12, padding: '5px 14px', cursor: 'pointer' }}
              >{t('cancel', { defaultValue: 'Cancel' })}</button>
              <button
                disabled={saving}
                onClick={() => renomearCampo(renomeando.root, renomeando.dom, renomeando.campo, novoNome.trim())}
                style={{ background: '#1d4ed8', border: 'none', borderRadius: 6, color: '#fff', fontSize: 12, padding: '5px 14px', cursor: 'pointer' }}
              >{t('contextMapPage.confirmRename', { defaultValue: 'Rename' })}</button>
            </div>
          </div>
        </div>
      )}

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
