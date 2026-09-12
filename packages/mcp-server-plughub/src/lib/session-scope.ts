/**
 * session-scope.ts — de quais POOLS uma sessão é, para o portão AUT-47.
 *
 * Vivia dentro do `server.ts` (função local `poolsDaSessaoViva`), alcançável só de
 * dentro daquele arquivo e por isso sem teste nenhum — a mesma razão que moveu a
 * política de máscara para `lib/context-masking.ts`.
 *
 * ── AUT-55, medido em 2026-09-12 ────────────────────────────────────────────
 *
 * Havia DUAS fontes: o pool que atende (do `meta`) e `core.pool.id` do ctx. Numa
 * tarefa DELEGADA as duas apontam o pool do WORKFLOW (`formfill_demo_ia`,
 * `wrapup_detached_ia`), enquanto o operador alcança o pool HUMANO onde
 * reivindicou o item (`formfill_demo`, `retencao_humano-int`). Resultado medido:
 * `[supervisor_state] 403 pool_scope` às dezenas, e o formulário não abre para
 * quem DETÉM a tarefa.
 *
 * ⚠️ O absurdo que isso produzia: o mesmo operador podia **submeter** o
 * formulário — o ingress de resume (A5) autoriza pela POSSE, conferida no
 * árbitro — e não podia **ler** o estado que o renderiza. Duas respostas para
 * "esta sessão é sua?" no mesmo fluxo, e a mais restritiva chegava primeiro.
 *
 * ⚠️ E sumia sozinho: quando a ativação do humano escrevia o `meta` com o pool
 * humano, a primeira fonte passava a bater. Por isso o dono viu *"deu erro
 * várias vezes e depois abriu"* — corrida, não intermitência de rede. Defeito
 * cujo sintoma desaparece é defeito que sobrevive.
 *
 * A terceira fonte é o **ledger do item** (`{tenant}:work_task:{sessionId}`,
 * campo `pool_id`): o pool onde o item está parqueado É um pool desta sessão.
 * Isso NÃO alarga `accessible_pools` — quem não alcança aquele pool continua
 * recusado; o que muda é a sessão deixar de esconder metade da própria
 * identidade. É a mesma direção do D5 (*a tela não é fonte de posse*): o fato
 * mora no servidor, e o portão passa a consultá-lo.
 */

export interface ScopeRedis {
  hget(chave: string, campo: string): Promise<string | null>
  get(chave: string): Promise<string | null>
}

/**
 * @param poolQueAtende pool do `meta` (quem atende AGORA), ou "" quando ausente
 * @returns pools distintos desta sessão; vazio = indeterminável (o chamador
 *          decide o que fazer — hoje 403 nomeado, ver `autorizaEscopoDaSessao`)
 */
export async function poolsDaSessao(
  redis:         ScopeRedis,
  tenantId:      string,
  sessionId:     string,
  poolQueAtende: string,
): Promise<string[]> {
  const pools = new Set<string>()
  if (poolQueAtende) pools.add(poolQueAtende)
  if (!tenantId) return [...pools]

  // Fonte 2 — o pool do WORKFLOW, do contexto.
  try {
    const bruto = await redis.hget(`${tenantId}:ctx:${sessionId}`, "core.pool.id")
    if (bruto) {
      const entrada = (JSON.parse(bruto) as { value?: unknown })?.value
      if (typeof entrada === "string" && entrada) pools.add(entrada)
    }
  } catch { /* tag ausente ou ilegível: as outras decidem */ }

  // Fonte 3 (AUT-55) — o pool onde o ITEM está parqueado. Ler o ledger aqui é
  // barato (uma chave) e não depende de o `meta` já ter sido escrito, que é
  // justamente a corrida que fazia o 403 ir e vir.
  try {
    const raw = await redis.get(`${tenantId}:work_task:${sessionId}`)
    if (raw) {
      const pool = (JSON.parse(raw) as { pool_id?: unknown })?.pool_id
      if (typeof pool === "string" && pool) pools.add(pool)
    }
  } catch { /* ledger ausente é o caminho NORMAL: contato sem item parqueado */ }

  return [...pools]
}
