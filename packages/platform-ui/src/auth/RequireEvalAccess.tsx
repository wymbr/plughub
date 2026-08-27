/**
 * RequireEvalAccess.tsx
 * Route guard for the Quality (evaluation) module — blocks direct-URL navigation
 * to pages whose backend endpoints are grant-first JWT-ABAC gated.
 *
 * Mirrors the Sidebar `passesAbac` STRICT branch exactly:
 *   - NO admin/supervisor bypass
 *   - empty/absent module_config = denied
 * The grant is checked against `session.moduleConfig` via the permissions helper.
 *
 * Usage:
 *   <RequireEvalAccess field="formularios"><FormsPage /></RequireEvalAccess>
 *   <RequireEvalAccess anyOf={['report', 'revisar', 'contestar']}><AvaliacoesPage /></RequireEvalAccess>
 */
import React from 'react'
import { useTranslation } from 'react-i18next'
import { useAuth } from './useAuth'
import { makePermissions, passesAbacRule } from '@/lib/permissions'

const MODULE = 'evaluation'

interface RequireEvalAccessProps {
  /** Single ABAC field to require (any access level). Ignored when `anyOf` is set. */
  field?: string
  /** Visible if the user has ANY one of these fields (OR). Takes precedence over `field`. */
  anyOf?: string[]
  children: React.ReactNode
}

export const RequireEvalAccess: React.FC<RequireEvalAccessProps> = ({ field, anyOf, children }) => {
  const { session } = useAuth()
  const { t } = useTranslation('evaluation')

  // Strict grant-first: build perms straight from the JWT module_config.
  // No admin/supervisor bypass; absent/empty config yields no grants → denied.
  const perms = makePermissions(session?.moduleConfig)

  const allowed = anyOf && anyOf.length > 0
    ? anyOf.some(f => perms.can(MODULE, f))
    : field ? perms.can(MODULE, field) : true

  if (!allowed) {
    return (
      <div className="flex flex-col items-center justify-center h-full text-muted-light gap-2 p-8 text-center">
        <p className="text-sm">{t('guard.noAccess')}</p>
      </div>
    )
  }

  return <>{children}</>
}

export default RequireEvalAccess


// ══════════════════════════════════════════════════════════════════════════════
// RequireAbac — guard de rota GENÉRICO (2026-08-27)
// ══════════════════════════════════════════════════════════════════════════════
//
// O problema que ele fecha: as rotas de `analise/*` (e irmãs) eram registradas NUAS
// em `app/routes.tsx` — o papel escondia o MENU e digitar a URL entrava. Dois erros
// em direções opostas: navegação restritiva demais, rota permissiva demais.
//
// ⚠️ Isto NÃO é a fronteira de autorização — essa é o escopo de pool no backend.
// É defesa em profundidade + coerência de UX: a rota passa a responder o MESMO que o
// menu, porque as duas chamam `passesAbacRule`. Enquanto os ~8 endpoints de conteúdo
// da (d) não tiverem portão próprio, quem digitar a URL de um DRILL ainda alcança o
// dado; o que muda é que a PÁGINA deixa de se abrir para quem o menu já negava.
//
// Escrito neste arquivo, e não num novo, de propósito: arquivo novo exige
// `build --no-cache` na imagem.

interface RequireAbacProps {
  module: string
  field?: string
  anyOf?: string[]
  children: React.ReactNode
}

export const RequireAbac: React.FC<RequireAbacProps> = ({
  module, field, anyOf, children,
}) => {
  const { session } = useAuth()
  const { t } = useTranslation('evaluation')

  // A prop `strict` saiu no passo 5: o portão é grant-first para todo mundo, então não
  // há mais dois comportamentos entre os quais escolher.
  const allowed = passesAbacRule(
    { module, field, anyOf },
    session?.moduleConfig,
    session?.role,
  )

  if (!allowed) {
    return (
      <div className="flex flex-col items-center justify-center h-full text-muted-light gap-2 p-8 text-center">
        <p className="text-sm">{t('guard.noAccess')}</p>
      </div>
    )
  }
  return <>{children}</>
}
