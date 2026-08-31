import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'
import { useAuth } from '@/auth/useAuth'

/**
 * EmptyScopeNotice — a tela DIZ que o vazio é escopo, em vez de só ficar vazia.
 *
 * ── Por que existe (AUT-10, 2026-08-31) ──────────────────────────────────────
 *
 * Depois da AUT-03, `accessible_pools: []` significa **nenhum pool** — e é config
 * VÁLIDA: pools são do tenant (criados pelo usuário), não da plataforma, então quem
 * entra pela primeira vez não tem nenhum até alguém atribuir.
 *
 * O problema não é a regra, é o SINTOMA: a tela fica vazia, e vazio é indistinguível
 * de "não há dado" e de "quebrou". É a mesma razão pela qual o resolvedor de escopo
 * loga a linha `dominio VAZIO` no servidor — *"não vejo nada" é o que chega ao
 * suporte*. Este componente é a metade da frente dessa mesma decisão.
 *
 * ── Por que a mensagem MUDA com quem lê ──────────────────────────────────────
 *
 * Quem detém `config.permissions` pode se atribuir pools; quem não detém, não — e
 * mandar essa pessoa para uma tela onde ela só levaria 403 seria pior que não dizer
 * nada. Então o texto diverge no ÚNICO ponto que importa: para onde a pessoa vai.
 *
 * ⚠️ Isto é AFORDÂNCIA, nunca fronteira. Quem decide é o servidor; esconder o link
 * apenas evita um caminho sem saída.
 */
export function EmptyScopeNotice({ compact = false }: { compact?: boolean }) {
  const { t } = useTranslation('common')
  const { perms } = useAuth()
  const podeConceder = perms.can('config', 'permissions', 'read_write')

  return (
    <div
      className={`rounded-lg border border-warning/40 bg-warning-light/40 ${
        compact ? 'px-3 py-2' : 'px-4 py-3'
      }`}
      role="status"
    >
      <p className={`font-medium text-warning-text ${compact ? 'text-xs' : 'text-sm'}`}>
        {t('emptyScope.title')}
      </p>
      <p className={`text-muted mt-0.5 leading-snug ${compact ? 'text-xs' : 'text-sm'}`}>
        {podeConceder ? t('emptyScope.canGrant') : t('emptyScope.askAdmin')}
      </p>
      {podeConceder && (
        <Link
          to="/config/access"
          className={`inline-block mt-1.5 text-primary hover:underline ${
            compact ? 'text-xs' : 'text-sm'
          }`}
        >
          {t('emptyScope.goToAccess')}
        </Link>
      )}
    </div>
  )
}
