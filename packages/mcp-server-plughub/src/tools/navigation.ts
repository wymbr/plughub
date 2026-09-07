/**
 * tools/navigation.ts
 * F3 do `docs/adr/adr-orchestrator-tree-navigation.md` (ORQ-03).
 *
 *   pool_route_resolve — traduz um CAMINHO da árvore de navegação no POOL que o
 *                        atende, lendo o mapa `navigation_pools` do pool da sessão.
 *
 * ── Por que a tradução é uma tool, e não um campo da folha ───────────────────
 *
 * A folha do `DialogForm` carrega o CAMINHO e nada mais (D2). O mapa
 * `caminho → pool` é config de POOL — DB-owned, editável na UI, com ciclo de vida
 * próprio —, enquanto a forma publicada é congelada no promote. Pôr o `pool_id`
 * dentro da forma criaria uma segunda fonte de verdade de roteamento, invisível de
 * quem administra pools, e transformaria o editor de formulário em editor de
 * roteamento — a linha que a D10 protege.
 *
 * A tradução aparece como um `invoke` no fluxo de propósito: fica visível, é
 * auditável, e tem um `on_failure` para onde ir. Escondê-la dentro do `escalate`
 * daria o mesmo despacho sem lugar para o erro aparecer.
 *
 * ⚠️ **Não existe default, e isso não é rigor: é a lição do `queue_pool_id or
 * pool_id`.** Um fallback de ENDEREÇO converte config ausente em despacho para o
 * lugar errado, sem nada ficar vermelho. Caminho sem prefixo declarado ⇒ `isError`
 * nomeando o caminho e as chaves que existiam.
 */

import { z }              from "zod"
import type { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js"
import type Redis         from "ioredis"

// ─── Dependências injetadas ───────────────────────────────────────────────────

export interface NavigationDeps {
  redis:            Redis
  agentRegistryUrl: string
  tenantId:         string
}

// ─── Schema ───────────────────────────────────────────────────────────────────

const PoolRouteResolveInputSchema = z.object({
  session_id: z.string().min(1),
  /** Caminho pontuado da árvore, como o `dialog_tree_level` o devolve em `category_path`. */
  path:       z.string().min(1),
  tenant_id:  z.string().optional(),
})

// ─── Casamento por prefixo mais longo ─────────────────────────────────────────

/**
 * Acha o pool para `caminho` no `mapa`, pelo prefixo mais longo.
 *
 * **Por que prefixo, e não igualdade.** Com igualdade o mapa precisa de uma entrada
 * por FOLHA, e cada folha nova na forma exigiria uma edição de config em outro
 * serviço para não quebrar — acoplamento entre conteúdo e roteamento que a D2 existe
 * para desfazer. Com prefixo, `sac` cobre a subárvore inteira e uma folha nova
 * herda o destino da pasta, que é o comportamento que se quer.
 *
 * **O prefixo é por SEGMENTO, nunca por caractere.** `sac_premium` não pode casar
 * com a chave `sac`: um `startsWith` cru faria isso, e o contato iria para o pool
 * errado com o log dizendo que casou.
 *
 * **A entrada mais específica vence** — `sac.especialista → retencao_humano` derrota
 * `sac → sac_ia` sem precisar reescrever a subárvore.
 */
export function matchNavigationRoute(
  mapa:    Record<string, string>,
  caminho: string,
): { pool: string; matched_key: string } | null {
  const segs = caminho.split(".").filter(s => s !== "")
  // Do mais específico para o mais geral: `a.b.c` → `a.b` → `a`.
  for (let n = segs.length; n > 0; n--) {
    const chave = segs.slice(0, n).join(".")
    const pool  = mapa[chave]
    if (typeof pool === "string" && pool.trim() !== "") {
      return { pool: pool.trim(), matched_key: chave }
    }
  }
  return null
}

// ─── Verbo: delegar ou escalar? ───────────────────────────────────────────────

/**
 * Um destino pode receber `delegate` sem pendurar o contato?
 *
 * **Delegar é SUSPENDER o chamador**, e ele só volta se o alvo chamar
 * `workflow_resume`. Um alvo que não devolve deixa o orquestrador suspenso até o
 * `timeout_hours`, e o modo de falha é o pior do catálogo: o cliente vê o
 * especialista atender normalmente, o especialista encerra o próprio segmento, e
 * **nada fica vermelho**.
 *
 * ⚠️ **DERIVADO do artefato, nunca declarado.** Um campo `delegavel: true` no
 * pool seria uma segunda fonte de verdade que envelhece calada — bastaria alguém
 * publicar um skill sem o retorno para a declaração virar mentira. Aqui a
 * resposta vem do snapshot que está PROMOVIDO naquele pool, que é o que roda.
 *
 * ⚠️ **A recusa é o default.** Só devolve `delegate` quando os três fatos são
 * positivos; qualquer dúvida (registry mudo, snapshot ausente, formato
 * inesperado) resolve para `escalate`, que é o comportamento de hoje e não
 * pendura ninguém.
 *
 * Os três motivos de recusa, cada um com nome — o gate
 * `probe_orchestrator_delegability.sh` usa exatamente estes:
 *   `sem_deploy`       o pool não tem slot `current` com snapshot (pools humanos)
 *   `nao_retorna`      o snapshot não invoca `workflow_resume`
 *   `cadeia_delegate`  o snapshot usa `delegate`, e `core.workflow.delegate_resume_token`
 *                      é tag ÚNICA da sessão: a delegação de dentro SOBRESCREVE o
 *                      token do orquestrador, que nunca retoma (CTR-06)
 */
export function decideVerb(snapshot: unknown): { verb: "delegate" | "escalate"; reason: string } {
  const flow = snapshot as { steps?: unknown[] } | null | undefined
  if (!flow || !Array.isArray(flow.steps) || flow.steps.length === 0) {
    return { verb: "escalate", reason: "sem_deploy" }
  }
  const passos = flow.steps.filter(
    (x): x is Record<string, unknown> => !!x && typeof x === "object",
  )
  if (passos.some(p => p["type"] === "delegate")) {
    return { verb: "escalate", reason: "cadeia_delegate" }
  }
  if (!passos.some(p => p["tool"] === "workflow_resume")) {
    return { verb: "escalate", reason: "nao_retorna" }
  }
  return { verb: "delegate", reason: "devolve_o_controle" }
}

// ─── Helpers ──────────────────────────────────────────────────────────────────

type ToolResult = {
  isError?: true
  content: Array<{ type: "text"; text: string }>
}

function ok(data: unknown): ToolResult {
  return { content: [{ type: "text" as const, text: JSON.stringify(data) }] }
}

function mcpError(code: string, message: string): ToolResult {
  return {
    isError: true,
    content: [{ type: "text" as const, text: JSON.stringify({ error: code, message }) }],
  }
}

// ─── Registro ─────────────────────────────────────────────────────────────────

export function registerNavigationTools(server: McpServer, deps: NavigationDeps): void {
  const { redis, agentRegistryUrl, tenantId: defaultTenantId } = deps

  server.tool(
    "pool_route_resolve",
    "Resolve a navigation tree path to the pool that serves it, using the " +
    "`navigation_pools` map of the SESSION's own pool. Matching is longest-prefix by " +
    "dot segment, so `sac` covers `sac.info_plano` and a more specific key wins. " +
    "There is no default: an unmapped path is an error naming the path, never a " +
    "dispatch to a guessed pool.",
    PoolRouteResolveInputSchema.shape as any,
    async (raw: Record<string, unknown>) => {
      let a: z.infer<typeof PoolRouteResolveInputSchema>
      try {
        a = PoolRouteResolveInputSchema.parse(raw)
      } catch (e) {
        if (e instanceof z.ZodError) {
          return mcpError("validation_error", e.errors.map(x => `${x.path.join(".")}: ${x.message}`).join("; "))
        }
        throw e
      }

      try {
        // O pool vem do META da sessão — nunca do chamador. Mesma razão do
        // `agent_event_record`: quem pergunta não escolhe de qual pool é a config
        // que responde.
        const metaRaw = await redis.get(`session:${a.session_id}:meta`)
        if (!metaRaw) {
          return mcpError(
            "session_not_found",
            `sessao ${a.session_id} sem meta no Redis — sem pool nao ha mapa a consultar`,
          )
        }
        const meta   = JSON.parse(metaRaw) as Record<string, unknown>
        const poolId = typeof meta["pool_id"] === "string" ? meta["pool_id"] : ""
        if (!poolId) {
          return mcpError("pool_unknown", `sessao ${a.session_id} sem pool_id no meta`)
        }
        const tenantId = a.tenant_id || (typeof meta["tenant_id"] === "string" ? meta["tenant_id"] : defaultTenantId)

        // Fonte CANÔNICA (agent-registry), não o cache de outro serviço: o mapa é
        // DB-owned e editável na UI, então uma edição tem de valer no próximo
        // contato — não no próximo TTL de um cache que não é nosso.
        const resp = await fetch(`${agentRegistryUrl}/v1/pools/${encodeURIComponent(poolId)}`, {
          headers: { "x-tenant-id": tenantId },
        })
        if (!resp.ok) {
          return mcpError(
            "registry_unavailable",
            `GET /v1/pools/${poolId} devolveu ${resp.status} — o mapa nao foi lido, e ` +
            `despachar sem le-lo seria adivinhar o destino`,
          )
        }
        const pool = await resp.json() as Record<string, unknown>
        const mapa = pool["navigation_pools"]

        if (mapa === null || mapa === undefined || typeof mapa !== "object") {
          return mcpError(
            "route_map_absent",
            `pool ${poolId} nao declara navigation_pools — o orquestrador nao tem para ` +
            `onde despachar caminho nenhum`,
          )
        }

        const achado = matchNavigationRoute(mapa as Record<string, string>, a.path)
        if (!achado) {
          const chaves = Object.keys(mapa as Record<string, string>)
          return mcpError(
            "route_not_found",
            `caminho "${a.path}" nao casa com prefixo nenhum de navigation_pools do pool ` +
            `${poolId} (${chaves.length} chave(s): ${chaves.slice(0, 12).join(", ")})`,
          )
        }

        // RET-02 — o VERBO vem junto do endereço, derivado do que está promovido
        // no destino. Uma consulta a mais, no mesmo lugar: se o chamador tivesse
        // de perguntar isso à parte, haveria dois momentos em que o mapa e o
        // artefato podem discordar, e o skill escolheria com base no mais velho.
        //
        // ⚠️ Falha ao ler o destino resolve para `escalate` com motivo NOMEADO —
        // nunca para `delegate`. Degradar para o verbo que suspende o chamador
        // seria transformar "não consegui perguntar" em contato pendurado.
        let verbo: { verb: "delegate" | "escalate"; reason: string }
        try {
          const rSlots = await fetch(
            `${agentRegistryUrl}/v1/pools/${encodeURIComponent(achado.pool)}/slots`,
            { headers: { "x-tenant-id": tenantId } },
          )
          if (!rSlots.ok) {
            verbo = { verb: "escalate", reason: `slots_indisponivel_${rSlots.status}` }
          } else {
            const corpo = await rSlots.json() as { slots?: { current?: { yaml_snapshot?: unknown } } }
            verbo = decideVerb(corpo?.slots?.current?.yaml_snapshot)
          }
        } catch (e) {
          verbo = { verb: "escalate", reason: "slots_erro" }
          console.warn(
            `[pool_route_resolve] nao consegui ler o deploy de ${achado.pool} ` +
            `(${e instanceof Error ? e.message : String(e)}) — verbo resolve para escalate`,
          )
        }

        return ok({
          pool:        achado.pool,
          matched_key: achado.matched_key,
          path:        a.path,
          source_pool: poolId,
          verb:        verbo.verb,
          verb_reason: verbo.reason,
        })
      } catch (err) {
        return mcpError("resolve_failed", err instanceof Error ? err.message : String(err))
      }
    },
  )
}
