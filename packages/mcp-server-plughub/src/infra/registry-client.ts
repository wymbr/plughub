/**
 * infra/registry-client.ts
 * Cliente HTTP para o Agent Registry.
 * Usado por agent_login para validar agent_type_id e obter max_concurrent_sessions + pools.
 *
 * Consome: GET /v1/agent-types/:agent_type_id  (header X-Tenant-Id: {tenant_id})
 * Spec: 4.5
 */

// ─── Tipos ────────────────────────────────────────────────────────────────────

export interface AgentTypeInfo {
  agent_type_id:           string
  max_concurrent_sessions: number
  execution_model:         string
  pools:                   string[]
  /**
   * Permissões MCP autorizadas, no formato `"{mcp_server}:{tool}"`
   * — ex: `["mcp-server-crm:customer_get"]`. Spec 4.6k.
   *
   * PROCEDÊNCIA: vem do `tools[]` DECLARADO na skill, no registry. Nunca do input
   * do `agent_login` e nunca de argumento de chamada — é o servidor que assina a
   * lista no `session_token`, e é assim que ela deixa de ser auto-declaração.
   * O defeito oposto (deixar o chamador nomear a própria autorização) foi o que
   * derrubou o gate de avaliador na CAP-01.
   *
   * ⚠️ A MESMA LISTA É LIDA POR TRÊS CONSUMIDORES COM TRÊS SEMÂNTICAS. Não é
   * ruído de documentação: decide o que popular quebra.
   *   1. `lib/invoke-audit.judgeInvoke` — match EXATO de `"{server}:{tool}"`;
   *      lista vazia ⇒ NEGA tudo.
   *   2. `sdk/src/proxy/server._isPermitted` (sidecar) — aceita curinga
   *      `"{server}:*"`; lista vazia ⇒ SEM filtro.
   *   3. `ai-gateway/inference.py:131` — casa o NOME CRU da tool
   *      (`t.get("name") in req.permissions`), **não** `"{server}:{tool}"`;
   *      lista vazia ⇒ sem filtro. Ou seja: popular no formato dos outros dois
   *      faria este filtro remover TODAS as tools.
   * Mitigante medido em 2026-09-01: `/v1/inference` **não tem chamador** no
   * repositório e `InferenceRequest.permissions` **nunca é setado** em Python —
   * o consumidor 3 é caminho morto. Se algum dia ganhar produtor, o formato tem
   * de ser unificado ANTES, não depois. Ver CAP-05/CAP-06 no `TODO.md`.
   */
  permissions:             string[]
}

export interface RegistryClient {
  getAgentType(tenantId: string, agentTypeId: string): Promise<AgentTypeInfo | null>
}

// ─── tools[] do registry → permissions[] do token ─────────────────────────────

/**
 * Mapeia o `tools[]` da skill (`{mcp_server, tool, required}`) para o formato que
 * o `judgeInvoke` exige: `"{mcp_server}:{tool}"`.
 *
 * DECISÕES, cada uma com o seu motivo:
 *
 * · **`required` NÃO filtra.** Ele responde *"a skill funciona sem esta tool?"* —
 *   pergunta de DEPENDÊNCIA, não de autorização. Usá-lo como gate negaria toda
 *   tool opcional e converteria um campo de robustez em política de acesso, que é
 *   a mistura de escopos que o `CLAUDE.md` proíbe.
 *
 * · **Entrada malformada é DESCARTADA COM LOG, nunca convertida em curinga nem em
 *   string torta.** `judgeInvoke` faz match exato, então `"undefined:foo"` viraria
 *   uma permissão que não casa com nada — falha silenciosa disfarçada de permissão
 *   concedida. Descartar barulhento deixa o defeito visível na hora.
 *
 * · **Não emite curinga `"{server}:*"`.** O sidecar o aceita e o `judgeInvoke` não;
 *   gerá-lo aqui produziria uma permissão que vale numa borda e não na outra. A
 *   assimetria está documentada nos dois lados e fecha-se de propósito, junto,
 *   quando for decisão de produto.
 *
 * · **Deduplica preservando a ordem** — a lista viaja no JWT e é ecoada na recusa
 *   (`Permissões autorizadas: [...]`) e no `AuditRecord.permissions_checked`;
 *   repetição só incharia os três.
 */
export function toPermissions(raw: unknown): string[] {
  if (!Array.isArray(raw)) return []

  const out: string[] = []
  const seen = new Set<string>()

  for (const entry of raw) {
    if (entry == null || typeof entry !== "object") {
      console.warn(`[registry-client] tool DESCARTADA — entrada não é objeto: ${JSON.stringify(entry)}`)
      continue
    }
    const e = entry as Record<string, unknown>
    const server = typeof e["mcp_server"] === "string" ? e["mcp_server"].trim() : ""
    const tool   = typeof e["tool"]       === "string" ? e["tool"].trim()       : ""
    if (!server || !tool) {
      console.warn(
        `[registry-client] tool DESCARTADA — mcp_server/tool ausente ou vazio: ${JSON.stringify(entry)}`
      )
      continue
    }
    const perm = `${server}:${tool}`
    if (seen.has(perm)) continue
    seen.add(perm)
    out.push(perm)
  }
  return out
}

// ─── Cliente de produção (HTTP) ───────────────────────────────────────────────

export function createRegistryClient(baseUrl: string): RegistryClient {
  return {
    async getAgentType(tenantId, agentTypeId) {
      // AgentType entity retired. In the deploy-driven model an agent's identity
      // IS its deployed skill_id, so agent_login validates against the skill.
      //
      // LÁPIDE do comentário anterior (removido 2026-09-01, CAP-06) — ele dizia
      // que a config por-agente "now lives on the pool's deploy slot" e que a lista
      // vazia era "the permissive default (⇒ no MCP tool filtering)". **As duas
      // afirmações eram falsas**, e cada uma escondia metade do defeito:
      //   · o `config_json` do slot só carrega `form_id` e `max_concurrent_sessions`
      //     — permissão nunca morou lá;
      //   · vazio é permissivo no sidecar e no ai-gateway, mas o `judgeInvoke` da
      //     borda `invoke` NEGA com lista vazia. O default não era permissivo: era
      //     `permission_denied` em 100% das chamadas, tornando INTRANSITÁVEL a
      //     única borda MCP que o `CLAUDE.md` declara em vigor (CAP-05).
      // Não doeu porque a população é zero (nenhum `external-mcp` configurado,
      // `session_timeline` vazia) — doeria no dia do primeiro operador a configurar.
      const url = `${baseUrl}/v1/skills/${encodeURIComponent(agentTypeId)}`
      const res = await fetch(url, {
        headers: {
          "X-Tenant-Id": tenantId,
          "Accept":      "application/json",
        },
      })
      if (res.status === 404) return null
      if (!res.ok) {
        throw new Error(`Agent Registry retornou ${res.status} para skill '${agentTypeId}'`)
      }
      const data = await res.json() as Record<string, unknown>

      return {
        agent_type_id:           (data["skill_id"] as string | undefined) ?? agentTypeId,
        max_concurrent_sessions: 1,
        execution_model:         "stateless",
        pools:                   [],
        permissions:             toPermissions(data["tools"]),
      }
    },
  }
}

// ─── Stub para testes ─────────────────────────────────────────────────────────

export function createStubRegistryClient(agentTypes: AgentTypeInfo[]): RegistryClient {
  return {
    async getAgentType(_tenantId, agentTypeId) {
      const found = agentTypes.find(a => a.agent_type_id === agentTypeId)
      if (!found) return null
      // Garante que campos adicionados retroativamente existem mesmo em stubs antigos
      if (!found.permissions)    found.permissions    = []
      if (!found.execution_model) found.execution_model = "stateless"
      return found
    },
  }
}
