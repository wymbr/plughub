/**
 * ChannelEndpointList.tsx
 * CRUD list of ChannelEndpoint records for a given channel type.
 *
 * Each row shows: identifier, pool, display_name, active status.
 * Inline form for create / edit (no modal).
 */
import React, { useState, useEffect, useCallback } from 'react'
import { useTranslation } from 'react-i18next'
import { useAuth } from '@/auth/useAuth'
import {
  listChannelEndpoints,
  createChannelEndpoint,
  updateChannelEndpoint,
  deleteChannelEndpoint,
  rotateChannelEndpointToken,
  revokeChannelEndpointToken,
  listPools,
} from '@/api/registry'
import type {
  ChannelEndpoint,
  ChannelEndpointChannel,
  ChannelEndpointOrigin,
  Pool,
} from '@/types'

// ── Procedência (ADR adr-webhook-endpoint-single-registry, D6) ────────────────
//
// `internal` é VISÍVEL e READ-ONLY. Não é restrição de permissão — é honestidade
// sobre quem manda: a linha nasce da declaração do ambiente (`infra/registry/
// *.yaml`), então editá-la aqui seria mentira de curta duração, desfeita no
// próximo boot do bridge (seed-if-absent repõe o que foi apagado). Melhor dizer
// que não se edita do que aceitar a edição e perdê-la em silêncio.
//
// Ausência do campo ⇒ `external`: respostas anteriores à coluna não têm
// procedência, e inventá-la seria pior que assumir o único caso que existia.
const originOf = (ep: ChannelEndpoint): ChannelEndpointOrigin => ep.origin ?? 'external'
const isReadOnly = (ep: ChannelEndpoint): boolean => originOf(ep) !== 'external'

const ORIGIN_BADGE: Record<ChannelEndpointOrigin, string> = {
  external:     'bg-surface-alt text-muted',
  internal:     'bg-secondary/10 text-secondary',
  legacy_token: 'bg-warning-light text-warning-text',
}

// ── Identifier placeholders (technical format, not translated) ─────────────────

const IDENTIFIER_PLACEHOLDER: Record<ChannelEndpointChannel, string> = {
  webchat:  'support',
  whatsapp: '+5511999999999',
  voice:    '+5511000000',
  sms:      '55119',
  email:    'support@company.com',
  webhook:  'salesforce',
}

// ── Form state type ────────────────────────────────────────────────────────────

interface FormState {
  identifier:   string
  pool_id:      string
  display_name: string
  active:       boolean
}

const emptyForm = (): FormState => ({
  identifier:   '',
  pool_id:      '',
  display_name: '',
  active:       true,
})

// ── Component ─────────────────────────────────────────────────────────────────

interface Props {
  channel: ChannelEndpointChannel
}

export const ChannelEndpointList: React.FC<Props> = ({ channel }) => {
  const { t } = useTranslation('channels')
  const { tenantId } = useAuth()

  const [endpoints, setEndpoints] = useState<ChannelEndpoint[]>([])
  const [pools,     setPools]     = useState<Pool[]>([])
  const [loading,   setLoading]   = useState(true)
  const [error,     setError]     = useState<string | null>(null)

  const [formMode, setFormMode]   = useState<null | 'new' | string>(null)
  const [form,     setForm]       = useState<FormState>(emptyForm())
  const [saving,   setSaving]     = useState(false)
  const [formErr,  setFormErr]    = useState<string | null>(null)

  // Token em claro recém-gerado. Vive APENAS aqui, em memória, até o operador
  // dispensar — o servidor guarda só o hash e não devolve o segredo nunca mais.
  // Nada de localStorage/sessionStorage: persistir no navegador anularia a decisão
  // de não persistir no servidor, e num terminal compartilhado é pior ainda.
  const [freshToken, setFreshToken] = useState<{ identifier: string; token: string } | null>(null)
  const [tokenBusy,  setTokenBusy]  = useState<string | null>(null)
  const [copied,     setCopied]     = useState(false)

  const load = useCallback(async () => {
    if (!tenantId) return
    setLoading(true); setError(null)
    try {
      const [eps, ps] = await Promise.all([
        listChannelEndpoints(tenantId, channel),
        listPools(tenantId).then(r => r.items ?? []),
      ])
      setEndpoints(eps)
      setPools(ps)
    } catch (e) {
      setError(String(e))
    } finally {
      setLoading(false)
    }
  }, [tenantId, channel])

  useEffect(() => { load() }, [load])

  // ── Form handlers ────────────────────────────────────────────────────────────

  function openNew() {
    setForm(emptyForm())
    setFormErr(null)
    setFormMode('new')
  }

  function openEdit(ep: ChannelEndpoint) {
    setForm({
      identifier:   ep.identifier,
      pool_id:      ep.pool_id,
      display_name: ep.display_name,
      active:       ep.active,
    })
    setFormErr(null)
    setFormMode(ep.id)
  }

  function closeForm() {
    setFormMode(null)
    setFormErr(null)
  }

  async function handleSave() {
    if (!tenantId) return
    if (!form.identifier.trim())   { setFormErr(t('errors.identifierRequired'));   return }
    if (!form.pool_id)             { setFormErr(t('errors.poolRequired'));          return }
    if (!form.display_name.trim()) { setFormErr(t('errors.displayNameRequired'));   return }

    setSaving(true); setFormErr(null)
    try {
      if (formMode === 'new') {
        await createChannelEndpoint({ ...form, channel }, tenantId)
      } else if (formMode) {
        await updateChannelEndpoint(formMode, {
          pool_id:      form.pool_id,
          display_name: form.display_name,
          active:       form.active,
        }, tenantId)
      }
      closeForm()
      await load()
    } catch (e) {
      setFormErr(String(e))
    } finally {
      setSaving(false)
    }
  }

  async function handleDelete(id: string) {
    if (!tenantId) return
    if (!confirm(t('endpoint.deleteConfirm'))) return
    try {
      await deleteChannelEndpoint(id, tenantId)
      await load()
    } catch (e) {
      setError(String(e))
    }
  }

  async function handleRotateToken(ep: ChannelEndpoint) {
    if (!tenantId) return
    // Confirmação só quando JÁ existe token: gerar o primeiro é aditivo, rotacionar
    // INVALIDA o anterior na hora e quebra quem estiver usando. Pedir confirmação nos
    // dois casos treinaria o operador a clicar em "ok" sem ler, que é como uma
    // confirmação deixa de proteger.
    if (ep.auth_required && !confirm(t('endpoint.token.rotateConfirm'))) return
    setTokenBusy(ep.id); setError(null)
    try {
      const r = await rotateChannelEndpointToken(ep.id, tenantId)
      if (r.token) { setFreshToken({ identifier: ep.identifier, token: r.token }); setCopied(false) }
      await load()
    } catch (e) {
      setError(String(e))
    } finally {
      setTokenBusy(null)
    }
  }

  async function handleRevokeToken(ep: ChannelEndpoint) {
    if (!tenantId) return
    // A confirmação diz o que REALMENTE acontece: revogar desliga a exigência junto,
    // e o endpoint volta a aceitar qualquer um. "Revoguei" lido como "protegi" seria
    // a pior leitura possível desta ação.
    if (!confirm(t('endpoint.token.revokeConfirm'))) return
    setTokenBusy(ep.id); setError(null)
    try {
      await revokeChannelEndpointToken(ep.id, tenantId)
      await load()
    } catch (e) {
      setError(String(e))
    } finally {
      setTokenBusy(null)
    }
  }

  async function copyFreshToken() {
    if (!freshToken) return
    try {
      await navigator.clipboard.writeText(freshToken.token)
      setCopied(true)
    } catch {
      // Clipboard bloqueado (contexto inseguro, permissão negada). Não é erro fatal:
      // o token está visível na tela e pode ser selecionado à mão. Falhar em silêncio
      // aqui é aceitável PORQUE o dado não se perde — o que não pode é o botão
      // parecer ter copiado sem ter copiado.
      setCopied(false)
    }
  }

  function poolLabel(poolId: string): string {
    const p = pools.find(x => x.pool_id === poolId)
    return p ? p.pool_id : poolId
  }

  // ── Render ───────────────────────────────────────────────────────────────────

  if (loading) return <p className="text-sm text-muted-light py-4">{t('loading')}</p>
  if (error)   return <p className="text-sm text-red-text py-4">⚠ {error}</p>

  const identifierHint = t(`identifierHints.${channel}`, t('form.identifierHintFallback'))

  return (
    <div className="space-y-4">

      {/* Header row */}
      <div className="flex items-center justify-between">
        <div>
          <p className="text-xs text-muted">{identifierHint}</p>
        </div>
        <button
          onClick={openNew}
          disabled={formMode !== null}
          className="px-3 py-1.5 rounded text-xs font-semibold bg-primary text-white hover:bg-primary-dark disabled:opacity-40 transition-colors"
        >
          {t('endpoint.add')}
        </button>
      </div>

      {/* ── Token recém-gerado: aparece UMA vez ────────────────────────────────
          Não é um toast que some sozinho, de propósito. Este é o único instante em
          que o segredo existe fora do hash; um aviso que desaparece por conta
          própria pode ser perdido por uma distração, e a recuperação não existe —
          só rotacionar de novo, invalidando o que acabou de ser gerado. Some apenas
          quando o operador diz que já guardou. */}
      {freshToken && (
        <div className="border border-warning rounded-lg bg-warning-light p-4 space-y-2">
          <p className="text-xs font-semibold text-warning-text">
            {t('endpoint.token.freshTitle', { identifier: freshToken.identifier })}
          </p>
          <p className="text-xs text-warning-text">{t('endpoint.token.freshWarning')}</p>
          <div className="flex items-center gap-2">
            <code className="flex-1 font-mono text-xs bg-surface border border-border rounded px-2 py-1.5 break-all">
              {freshToken.token}
            </code>
            <button
              onClick={copyFreshToken}
              className="px-3 py-1.5 rounded text-xs font-semibold bg-primary text-white hover:bg-primary-dark transition-colors"
            >
              {copied ? t('endpoint.token.copied') : t('endpoint.token.copy')}
            </button>
          </div>
          <button
            onClick={() => { setFreshToken(null); setCopied(false) }}
            className="text-xs text-muted hover:text-dark"
          >
            {t('endpoint.token.dismiss')}
          </button>
        </div>
      )}

      {/* Create form */}
      {formMode === 'new' && (
        <EndpointForm
          form={form}
          setForm={setForm}
          pools={pools}
          channel={channel}
          placeholder={IDENTIFIER_PLACEHOLDER[channel]}
          identifierReadonly={false}
          saving={saving}
          error={formErr}
          onSave={handleSave}
          onCancel={closeForm}
        />
      )}

      {/* List */}
      {endpoints.length === 0 && formMode !== 'new' ? (
        <p className="text-sm text-muted-light py-2">{t('endpoint.noEndpointsStandalone')}</p>
      ) : (
        <table className="w-full text-sm border-collapse">
          <thead>
            <tr className="text-left text-xs text-muted border-b border-border">
              <th className="py-2 pr-4 font-medium">{t('endpoint.colIdentifier')}</th>
              <th className="py-2 pr-4 font-medium">{t('endpoint.colPool')}</th>
              <th className="py-2 pr-4 font-medium">{t('endpoint.colDisplayName')}</th>
              <th className="py-2 pr-4 font-medium">{t('endpoint.colOrigin')}</th>
              <th className="py-2 pr-4 font-medium">{t('endpoint.colAuth')}</th>
              <th className="py-2 pr-4 font-medium">{t('endpoint.colStatus')}</th>
              <th className="py-2 font-medium"></th>
            </tr>
          </thead>
          <tbody>
            {endpoints.map(ep => (
              <React.Fragment key={ep.id}>
                <tr className="border-b border-border hover:bg-surface-muted">
                  <td className="py-2.5 pr-4 font-mono text-xs text-dark">{ep.identifier}</td>
                  <td className="py-2.5 pr-4 text-xs text-muted">{poolLabel(ep.pool_id)}</td>
                  <td className="py-2.5 pr-4 text-xs">{ep.display_name}</td>
                  <td className="py-2.5 pr-4">
                    <span
                      className={`text-xs px-2 py-0.5 rounded-full font-medium ${ORIGIN_BADGE[originOf(ep)]}`}
                      title={t(`endpoint.originHint.${originOf(ep)}`)}
                    >
                      {t(`endpoint.origin.${originOf(ep)}`)}
                    </span>
                  </td>
                  {/* Autenticação — a AUSÊNCIA é o que precisa aparecer.
                      `auth_required` nasce false para não quebrar nada, e o risco
                      conhecido do opt-in é virar proteção que ninguém liga. O
                      antídoto escolhido não é o default agressivo: é esta coluna
                      (e a contagem no probe). Endpoint anônimo fica DITO, não
                      subentendido — mesmo movimento da Fase A do ADR. */}
                  <td className="py-2.5 pr-4">
                    {ep.auth_required ? (
                      <span
                        className="text-xs px-2 py-0.5 rounded-full font-medium bg-green-light text-green-text"
                        title={ep.token_prefix ? `${ep.token_prefix}…` : undefined}
                      >
                        {t('endpoint.auth.required')}
                      </span>
                    ) : (
                      <span
                        className="text-xs px-2 py-0.5 rounded-full font-medium bg-warning-light text-warning-text"
                        title={t('endpoint.auth.anonymousHint')}
                      >
                        {t('endpoint.auth.anonymous')}
                      </span>
                    )}
                  </td>
                  <td className="py-2.5 pr-4">
                    <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${
                      ep.active
                        ? 'bg-green-light text-green-text'
                        : 'bg-surface-alt text-muted'
                    }`}>
                      {ep.active ? t('status.active') : t('status.inactive')}
                    </span>
                  </td>
                  <td className="py-2.5 flex gap-2 justify-end">
                    {isReadOnly(ep) ? (
                      // D6: declarado, não cadastrado. Diz POR QUE não edita — um
                      // botão apenas desabilitado leria como falta de permissão.
                      //
                      // ⚠️ Esta célula NÃO repete a procedência. A v1 dizia "declarado"
                      // aqui e no badge de Origem, e a linha saía com a mesma palavra
                      // duas vezes — o leitor via um segundo carimbo de origem, não a
                      // ausência de ação. Coluna de AÇÕES responde "o que dá para
                      // fazer", não "o que isto é".
                      <span className="text-xs text-muted-light" title={t('endpoint.readOnlyHint')}>
                        {t('endpoint.readOnly')}
                      </span>
                    ) : (
                      <>
                        {/* Token só nas linhas `external`. Ligar auth num endpoint
                            INTERNO silenciaria o disparo interno — `workflow_trigger`,
                            o proxy da workflow-api e o bridge não enviam credencial
                            (fatia 3). Oferecer o botão ali seria um gatilho para um
                            defeito que a tela não explica; o ramo read-only acima diz
                            por que a linha não se edita. */}
                        <button
                          onClick={() => handleRotateToken(ep)}
                          disabled={formMode !== null || tokenBusy === ep.id}
                          className="text-xs text-secondary hover:text-primary disabled:opacity-40"
                          title={t('endpoint.token.rotateHint')}
                        >
                          {tokenBusy === ep.id
                            ? t('actions.saving')
                            : ep.auth_required
                              ? t('endpoint.token.rotate')
                              : t('endpoint.token.generate')}
                        </button>
                        {ep.auth_required && (
                          <button
                            onClick={() => handleRevokeToken(ep)}
                            disabled={formMode !== null || tokenBusy === ep.id}
                            className="text-xs text-warning-text hover:text-red-text disabled:opacity-40"
                            title={t('endpoint.token.revokeHint')}
                          >
                            {t('endpoint.token.revoke')}
                          </button>
                        )}
                        <button
                          onClick={() => openEdit(ep)}
                          disabled={formMode !== null}
                          className="text-xs text-secondary hover:text-primary disabled:opacity-40"
                        >
                          {t('actions.edit')}
                        </button>
                        <button
                          onClick={() => handleDelete(ep.id)}
                          disabled={formMode !== null}
                          className="text-xs text-red hover:text-red-text disabled:opacity-40"
                        >
                          {t('actions.delete')}
                        </button>
                      </>
                    )}
                  </td>
                </tr>

                {/* Inline edit form */}
                {formMode === ep.id && (
                  <tr>
                    <td colSpan={7} className="pb-3 pt-1">
                      <EndpointForm
                        form={form}
                        setForm={setForm}
                        pools={pools}
                        channel={channel}
                        placeholder={IDENTIFIER_PLACEHOLDER[channel]}
                        identifierReadonly={true}
                        saving={saving}
                        error={formErr}
                        onSave={handleSave}
                        onCancel={closeForm}
                      />
                    </td>
                  </tr>
                )}
              </React.Fragment>
            ))}
          </tbody>
        </table>
      )}

      {channel === 'webhook' && <PoolAddressNote pools={pools} />}
    </div>
  )
}

// ── Endereço implícito por pool (ADR §7 "em aberto") ──────────────────────────
//
// O critério de reprovação da Fase D é *"endpoint acionável ausente da tela"*, e
// sem esta seção ele reprovaria: além do identificador registrado, **todo pool
// webhook é acionável em `/v1/channels/webhook/pool/{pool_id}`** — dez endereços
// vivos que a tabela acima não mostra, porque não têm linha no registro.
//
// POR QUE MOSTRAR E NÃO REGISTRAR. Criar uma linha `identifier = pool_id → pool_id`
// seria a função identidade do pool: um registro que não pode discordar da fonte,
// logo um inventário que não pode denunciar nada (o mesmo argumento que fez as
// linhas internas serem DECLARADAS e não derivadas, §7.1). O endereço por pool
// também não tem o defeito do §2 — ele já endereça a unidade canônica, e não fica
// ambíguo quando um skill está deployado em N pools.
//
// Então: o inventário fica completo na TELA (que é onde a pergunta *"quais URLs
// disparam?"* é feita) sem inflar o REGISTRO com linhas tautológicas.
const PoolAddressNote: React.FC<{ pools: Pool[] }> = ({ pools }) => {
  const { t } = useTranslation('channels')
  const webhookPools = pools.filter(p => (p.channel_types ?? []).includes('webhook'))
  if (webhookPools.length === 0) return null

  return (
    <div className="border-t border-border pt-4 mt-2">
      <p className="text-xs font-medium text-dark mb-1">{t('endpoint.poolAddressTitle')}</p>
      <p className="text-xs text-muted-light mb-3">{t('endpoint.poolAddressNote')}</p>
      <ul className="space-y-1">
        {webhookPools.map(p => (
          <li key={p.pool_id} className="font-mono text-xs text-muted">
            POST /v1/channels/webhook/pool/{p.pool_id}
          </li>
        ))}
      </ul>
    </div>
  )
}

// ── EndpointForm ───────────────────────────────────────────────────────────────

interface FormProps {
  form:               FormState
  setForm:            React.Dispatch<React.SetStateAction<FormState>>
  pools:              Pool[]
  channel:            ChannelEndpointChannel
  placeholder:        string
  identifierReadonly: boolean
  saving:             boolean
  error:              string | null
  onSave:             () => void
  onCancel:           () => void
}

function EndpointForm({
  form, setForm, pools, placeholder, identifierReadonly,
  saving, error, onSave, onCancel,
}: FormProps) {
  const { t } = useTranslation('channels')
  const inp = 'text-xs border border-border-strong rounded px-2 py-1.5 focus:outline-none focus:border-primary w-full'

  return (
    <div className="bg-surface-muted border border-border rounded-lg p-4 space-y-3">
      <div className="grid grid-cols-2 gap-3">
        <div>
          <label className="text-xs font-medium text-dark block mb-1">{t('form.identifier')}</label>
          <input
            className={inp + (identifierReadonly ? ' bg-surface-alt text-muted' : '')}
            value={form.identifier}
            placeholder={placeholder}
            readOnly={identifierReadonly}
            onChange={e => setForm(p => ({ ...p, identifier: e.target.value }))}
          />
        </div>
        <div>
          <label className="text-xs font-medium text-dark block mb-1">{t('form.pool')}</label>
          <select
            className={inp}
            value={form.pool_id}
            onChange={e => setForm(p => ({ ...p, pool_id: e.target.value }))}
          >
            <option value="">{t('form.selectPool')}</option>
            {pools.map(p => (
              <option key={p.pool_id} value={p.pool_id}>{p.pool_id}</option>
            ))}
          </select>
        </div>
        <div>
          <label className="text-xs font-medium text-dark block mb-1">{t('form.displayName')}</label>
          <input
            className={inp}
            value={form.display_name}
            placeholder={t('form.displayNamePlaceholder')}
            onChange={e => setForm(p => ({ ...p, display_name: e.target.value }))}
          />
        </div>
        <div className="flex items-end pb-1">
          <label className="flex items-center gap-2 text-xs cursor-pointer">
            <input
              type="checkbox"
              checked={form.active}
              onChange={e => setForm(p => ({ ...p, active: e.target.checked }))}
            />
            {t('form.active')}
          </label>
        </div>
      </div>

      {error && <p className="text-xs text-red-text">{error}</p>}

      <div className="flex gap-2">
        <button
          onClick={onSave}
          disabled={saving}
          className="px-3 py-1.5 rounded text-xs font-semibold bg-primary text-white disabled:opacity-40 hover:bg-primary-dark transition-colors"
        >
          {saving ? t('actions.saving') : t('actions.save')}
        </button>
        <button
          onClick={onCancel}
          className="px-3 py-1.5 rounded text-xs border border-border-strong text-muted hover:text-dark transition-colors"
        >
          {t('actions.cancel')}
        </button>
      </div>
    </div>
  )
}

export default ChannelEndpointList
