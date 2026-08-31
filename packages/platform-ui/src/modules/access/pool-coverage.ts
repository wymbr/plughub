/**
 * pool-coverage.ts — quantos usuários ATIVOS alcançam cada pool (AUT-14).
 *
 * ── Por que isto existe ───────────────────────────────────────────────────────
 *
 * "Selecionar todos" é INSTANTÂNEO, não regra: concede os N pools de hoje e não o N+1
 * de amanhã. E depois da AUT-03 (`accessible_pools: []` = NENHUM pool) um pool pode
 * acabar sem nenhum usuário no escopo — e isso **não o desliga**. O roteamento não
 * consulta `accessible_pools`: o pool continua recebendo contato, enfileirando e
 * consumindo licença, **invisível** no Monitor, no Console e nos relatórios. Não é um
 * pool morto; é um pool **sem vigia**, e o sintoma é AUSÊNCIA — a linha que ninguém vê.
 *
 * ── O que é ALARME e o que é DADO (decisão do dono, 2026-08-31) ───────────────
 *
 * Alarme: **só órfão** (zero usuários ativos). Contar "preso a UM usuário" como alarme
 * repetiria a D14.1 do `CLAUDE.md` — publicar EXPOSIÇÃO como se fosse DANO. E o número
 * medido na instalação (31 de 36) é **86% da população**: alarme que dispara em 86% dos
 * casos não é sinal, é o portão permanentemente vermelho que ensina a ser ignorado.
 *
 * O grau de cobertura sobrevive como **contagem por pool** — dado disponível a quem já
 * está olhando, nunca emblema.
 *
 * ── Onde exposição vira dano ──────────────────────────────────────────────────
 *
 * Num instante nomeável: a **desativação** de um usuário, ou a remoção de pools do
 * escopo dele. É por isso que `orphansAfter` existe. E o fato relevante ali é do
 * TENANT, não do pool: *"31 pools dependem da MESMA conta"* — cada linha isolada parece
 * normal, porque é normal.
 *
 * ── Regras que o cálculo NÃO pode errar ───────────────────────────────────────
 *
 *  · usuário INATIVO não vigia nada — contá-lo faria um pool órfão parecer coberto;
 *  · o escopo é sempre uma LISTA (não há mais "vazio = todos", AUT-03), então nenhuma
 *    conta "cobre tudo" implicitamente;
 *  · pool sem linha no mapa é órfão, não ausente: `byPool` traz TODOS os pools, com
 *    zero explícito. Chave faltando viraria `undefined` e o `undefined === 0` do
 *    consumidor decidiria errado sem reclamar.
 */

export interface CoverageUser {
  id:               string
  active:           boolean
  accessible_pools: string[]
}

export interface Coverage {
  /** pool_id → nº de usuários ATIVOS que o alcançam. Todo pool tem chave, mesmo com 0. */
  byPool:  Record<string, number>
  /**
   * Pools com zero usuários ativos — o único ALARME.
   *
   * **Vazio quando `truncated`**: com a população incompleta um pool coberto por alguém
   * que não veio na página apareceria como órfão, e alarme falso é como se ensina a
   * ignorar alarme. Ausência declarada, nunca número plausível.
   */
  orphans: string[]
  /**
   * A população usada no cálculo pode estar CORTADA — e aí nenhum órfão é afirmável.
   *
   * `GET /auth/users` pagina com `limit` (default **100**, medido em 2026-08-31) e a
   * tela pede a lista sem paginar. Enquanto o tenant couber na página o censo é exato;
   * no dia em que passar, o silêncio seria uma lista de órfãos inventados.
   */
  truncated: boolean
}

/**
 * @param truncated população possivelmente incompleta (ver `Coverage.truncated`).
 */
export function computeCoverage(users: CoverageUser[], poolIds: string[], truncated = false): Coverage {
  const byPool: Record<string, number> = {}
  // Semeia com zero explícito ANTES de contar: é o que separa "pool sem ninguém" de
  // "pool que o cálculo não viu".
  for (const p of poolIds) byPool[p] = 0

  for (const u of users) {
    if (!u.active) continue
    for (const p of u.accessible_pools ?? []) {
      // Só pools que existem — escopo pode carregar pool desativado/removido, e contá-lo
      // inflaria a cobertura de um pool que não está na lista.
      //
      // Lê o VALOR em vez de testar a chave (`p in byPool`): com `noUncheckedIndexedAccess`
      // é o compilador quem exige, mas a razão é a mesma da AUT-03 — presença de chave e
      // valor são fatos diferentes, e decidir pela chave deixa o valor sem conferência.
      const atual = byPool[p]
      if (atual !== undefined) byPool[p] = atual + 1
    }
  }

  // Com população cortada o mapa ainda serve de DADO (a contagem por pool é um piso
  // honesto: "ao menos N"), mas o ALARME não — ele afirma AUSÊNCIA, e ausência não se
  // deduz de uma amostra.
  return {
    byPool,
    orphans: truncated ? [] : poolIds.filter((p) => byPool[p] === 0),
    truncated,
  }
}

/**
 * Quais pools ficariam ÓRFÃOS se a mudança pendente fosse salva.
 *
 * Devolve só os pools que **passam** de coberto a órfão — nunca os que já estavam
 * órfãos antes. Avisar sobre um órfão preexistente ao desativar alguém sem relação com
 * ele culparia a mudança errada, e o aviso viraria ruído no primeiro uso.
 *
 * `pendente.accessiblePools` é o escopo DEPOIS da edição; `pendente.active`, o estado
 * depois. Passar o estado final (e não um delta) mantém uma fonte só de verdade sobre o
 * que vai ser salvo.
 */
export function orphansAfter(
  users:    CoverageUser[],
  poolIds:  string[],
  pendente: { id: string; active: boolean; accessiblePools: string[] },
): string[] {
  const antes = computeCoverage(users, poolIds)

  const depoisUsers = users.map((u) =>
    u.id === pendente.id
      ? { ...u, active: pendente.active, accessible_pools: pendente.accessiblePools }
      : u,
  )
  // Usuário NOVO (ainda não está na lista) só pode ACRESCENTAR cobertura — mas incluí-lo
  // mantém a função correta se um dia for chamada na criação.
  if (!users.some((u) => u.id === pendente.id)) {
    depoisUsers.push({ id: pendente.id, active: pendente.active, accessible_pools: pendente.accessiblePools })
  }

  const depois = computeCoverage(depoisUsers, poolIds)
  // Se a população veio cortada, `orphansAfter` também não pode afirmar — mas aqui a
  // conta é um DELTA entre dois estados da MESMA amostra, então um pool que perde o
  // último vigia CONHECIDO segue sendo um aviso legítimo. O que se perde é a certeza de
  // que não há outro vigia fora da amostra; por isso quem chama passa a amostra inteira.
  return poolIds.filter((p) => (antes.byPool[p] ?? 0) > 0 && depois.byPool[p] === 0)
}
