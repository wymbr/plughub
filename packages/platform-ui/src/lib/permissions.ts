/**
 * permissions.ts
 * Helper de verificação ABAC client-side.
 *
 * Lê module_config do JWT (via session.moduleConfig) e responde localmente
 * sem round-trip ao servidor.
 *
 * Exemplos de uso:
 *
 *   const { session } = useAuth()
 *   const perms = makePermissions(session)
 *
 *   perms.can('evaluation', 'contestar')               // tem acesso (qualquer)?
 *   perms.can('evaluation', 'contestar', 'read_write') // tem read_write?
 *   perms.can('evaluation', 'contestar', 'read_write', 'pool:retencao_humano')
 *   perms.access('evaluation', 'contestar')            // 'none' | 'read_only' | ...
 *   perms.scopeOf('evaluation', 'contestar')           // [] = global; [...] = pools
 */

import type { ModuleConfig, ModuleFieldConfig, PermissionAccess } from '@/types'

// Hierarquia de acesso: um nível mais alto inclui os anteriores.
const ACCESS_LEVELS: PermissionAccess[] = ['none', 'read_only', 'write_only', 'read_write']

function accessLevel(access: PermissionAccess): number {
  return ACCESS_LEVELS.indexOf(access)
}

export interface Permissions {
  /**
   * Verifica se o usuário tem acesso ao campo `field` do módulo `moduleId`.
   *
   * @param moduleId   — ex: 'evaluation'
   * @param field      — ex: 'contestar'
   * @param minAccess  — nível mínimo requerido (default: qualquer acesso != none)
   * @param scopeValue — pool_id ou campaign_id a verificar (ex: 'pool:retencao_humano').
   *                     Se omitido, qualquer escopo (ou global) basta.
   */
  can(
    moduleId: string,
    field: string,
    minAccess?: PermissionAccess,
    scopeValue?: string,
  ): boolean

  /** Retorna o nível de acesso configurado para o campo (default: 'none'). */
  access(moduleId: string, field: string): PermissionAccess

  /**
   * Retorna os valores de escopo do campo.
   * [] significa acesso global (sem restrição de pool/campaign).
   */
  scopeOf(moduleId: string, field: string): string[]

  /**
   * Retorna true se o usuário tem acesso global ao campo
   * (scope = [] significa acesso a todos os pools).
   */
  isGlobal(moduleId: string, field: string): boolean

  /** Retorna a config raw do campo, ou undefined se não configurado. */
  fieldConfig(moduleId: string, field: string): ModuleFieldConfig | undefined
}

/**
 * Cria um helper de permissões a partir do moduleConfig do JWT.
 * É puro (sem efeitos colaterais) — seguro para chamar em render.
 */
export function makePermissions(moduleConfig: ModuleConfig | undefined | null): Permissions {
  const cfg: ModuleConfig = moduleConfig ?? {}

  function fieldConfig(moduleId: string, field: string): ModuleFieldConfig | undefined {
    return cfg[moduleId]?.[field]
  }

  function access(moduleId: string, field: string): PermissionAccess {
    return fieldConfig(moduleId, field)?.access ?? 'none'
  }

  function scopeOf(moduleId: string, field: string): string[] {
    return fieldConfig(moduleId, field)?.scope ?? []
  }

  function isGlobal(moduleId: string, field: string): boolean {
    const fc = fieldConfig(moduleId, field)
    if (!fc) return false
    return fc.scope.length === 0   // [] = sem restrição de escopo = global
  }

  function can(
    moduleId: string,
    field: string,
    minAccess: PermissionAccess = 'read_only',
    scopeValue?: string,
  ): boolean {
    const fc = fieldConfig(moduleId, field)
    if (!fc) return false

    // Verifica nível de acesso
    if (accessLevel(fc.access) < accessLevel(minAccess)) return false

    // Sem scopeValue especificado → basta ter acesso (global ou qualquer pool)
    if (!scopeValue) return true

    // [] = acesso global → qualquer scopeValue passa
    if (fc.scope.length === 0) return true

    // Verifica se o scopeValue está na lista de escopos autorizados
    return fc.scope.includes(scopeValue)
  }

  return { can, access, scopeOf, isGlobal, fieldConfig }
}

/**
 * Hook convenience — cria as permissões a partir do session.moduleConfig.
 * Uso recomendado dentro de componentes React.
 *
 * @example
 *   const perms = usePermissions()
 *   if (perms.can('evaluation', 'contestar', 'read_write')) { ... }
 */
export function usePermissionsOf(moduleConfig: ModuleConfig | undefined | null): Permissions {
  return makePermissions(moduleConfig)
}


// ══════════════════════════════════════════════════════════════════════════════
// Regra ABAC de navegação — UMA casa, dois consumidores
// ══════════════════════════════════════════════════════════════════════════════
//
// Até 2026-08-27 esta regra existia só dentro do `passesAbac` do `Sidebar.tsx`, e as
// rotas de `analise/*` não tinham guard NENHUM (`app/routes.tsx` as registrava nuas):
// o papel escondia o MENU, e digitar a URL entrava. Eram dois erros em direções
// opostas — navegação restritiva demais, rota permissiva demais.
//
// Ao dar guard à rota, a tentação é reimplementar a decisão lá. Seriam duas portas
// medindo a mesma regra, que é como a divergência de masking nasceu (ver CHANGELOG
// § V2: três implementações, cada uma com teste próprio, e nenhum comparando as
// portas entre si). Por isso o predicado mora aqui e os dois o chamam.
//
// ⚠️ O ramo NÃO-STRICT carrega um bypass de admin/supervisor que contradiz a decisão
// do dono de 2026-08-26 ("o admin respeita a ABAC como qualquer um"). Ele é
// PRESERVADO aqui de propósito: mudá-lo altera todo item não-strict de uma vez, e é
// decisão própria — o que este movimento faz é apenas garantir que MENU e ROTA
// respondam a mesma coisa. Fazer as duas mudanças juntas tornaria qualquer regressão
// ambígua entre "o guard está errado" e "a semântica mudou".

export interface AbacNavRule {
  module: string
  field?: string
  anyOf?: string[]
  /** grant-first: exige o grant mesmo com config vazio e mesmo para admin. */
  strict?: boolean
}

export function passesAbacRule(
  rule: AbacNavRule | undefined,
  moduleConfig: ModuleConfig | undefined | null,
  role: string | undefined,
): boolean {
  if (!rule) return true
  const strict = rule.strict === true
  if (!strict) {
    if (!moduleConfig || Object.keys(moduleConfig).length === 0) return true
    if (['admin', 'supervisor'].includes(role ?? '')) return true
  }
  const perms = makePermissions(moduleConfig)
  if (rule.anyOf && rule.anyOf.length > 0) return rule.anyOf.some(f => perms.can(rule.module, f))
  return rule.field ? perms.can(rule.module, rule.field) : true
}
