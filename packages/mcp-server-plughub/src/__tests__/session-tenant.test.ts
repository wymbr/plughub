/**
 * session-tenant.test.ts
 * `resolveSessionTenant` — o resolvedor único de tenant a partir de `session:{id}:meta`.
 *
 * POR QUE ELE EXISTE E POR QUE TEM TESTE PRÓPRIO (2026-08-21):
 * três rotas HTTP do mcp-server resolviam o tenant da mesma chave, cada uma com a
 * sua cópia, e as três nasciam iguais:
 *
 *     let tenantId = process.env["PLUGHUB_TENANT_ID"] ?? "tenant_demo"
 *     try { ...ler meta... } catch { /* use fallbacks *\/ }
 *
 * É o defeito que o `conversation_escalate` teve até 2026-08-18, com outro literal,
 * sobrevivendo em `session_transfer` (que ESCREVE: publica roteamento),
 * `supervisor_capabilities` e `copilot_state` (que LEEM config de pool e
 * ContextStore usando o tenant como prefixo de chave).
 *
 * **Identidade não tem fallback.** Um tenant inventado não degrada o resultado —
 * corrompe a fronteira. Na escrita, re-roteia o contato para um namespace sem
 * instância nenhuma DEPOIS de o cliente ver o "participant_left"; nas leituras,
 * devolve dados de outro tenant. Por isso o contrato aqui é `tenantId: null` +
 * MOTIVO, nunca um default.
 *
 * O motivo não é enfeite: "Redis falhou" e "sessão sem meta" pedem ações opostas, e
 * a versão anterior as colapsava na mesma ausência muda.
 */

import { describe, it, expect } from "vitest"
import { resolveSessionTenant } from "../server"

const SID = "sess_20260821T120000_01HX5K3MNJP8QVWZ4RABC"

/** Redis mínimo: devolve o que o teste mandar, ou explode se pedido. */
function fakeRedis(value: string | null, explode = false) {
  return {
    get: async (_key: string) => {
      if (explode) throw new Error("ECONNREFUSED")
      return value
    },
  }
}

describe("resolveSessionTenant — identidade não tem fallback", () => {
  // ── CONTROLE ────────────────────────────────────────────────────────────────
  // Sem ele, um resolvedor que devolvesse `null` sempre passaria em todas as
  // recusas abaixo sem provar nada.
  it("CONTROLE: meta bem-formado devolve o tenant e nenhum motivo", async () => {
    const r = await resolveSessionTenant(
      fakeRedis(JSON.stringify({ tenant_id: "tenant_a", channel: "webchat", pool_id: "p1" })),
      SID, "test",
    )
    expect(r.tenantId).toBe("tenant_a")
    expect(r.reason).toBe("")
    expect(r.meta["pool_id"]).toBe("p1")
  })

  it("meta AUSENTE → null, e o motivo diz qual chave faltou", async () => {
    const r = await resolveSessionTenant(fakeRedis(null), SID, "test")
    expect(r.tenantId).toBeNull()
    expect(r.reason).toContain("AUSENTE")
    expect(r.reason).toContain(SID)
  })

  it("meta SEM `tenant_id` → null (era o caso que caía no literal)", async () => {
    const r = await resolveSessionTenant(
      fakeRedis(JSON.stringify({ channel: "webchat", contact_id: "c1" })), SID, "test",
    )
    expect(r.tenantId).toBeNull()
    expect(r.reason).toContain("SEM")
    // O resto do meta continua utilizável por quem quiser diagnosticar.
    expect(r.meta["channel"]).toBe("webchat")
  })

  it("meta MALFORMADO → null, e o motivo o distingue da ausência", async () => {
    const r = await resolveSessionTenant(fakeRedis("{isto não é json"), SID, "test")
    expect(r.tenantId).toBeNull()
    expect(r.reason).toContain("leitura/parse")
  })

  it("meta que é JSON VÁLIDO mas não é objeto → null", async () => {
    // `JSON.parse("[1,2]")` não lança; sem este ramo o array viraria "meta" e
    // `meta["tenant_id"]` daria undefined pelo caminho errado.
    const r = await resolveSessionTenant(fakeRedis("[1,2,3]"), SID, "test")
    expect(r.tenantId).toBeNull()
    expect(r.reason).toContain("não é objeto")
  })

  it("FALHA de Redis → null, com motivo DIFERENTE do de ausência", async () => {
    // As duas exigem ações opostas — reconectar × consertar o produtor do meta —
    // e colapsá-las na mesma ausência muda foi metade do defeito original.
    const ausente = await resolveSessionTenant(fakeRedis(null), SID, "test")
    const falha   = await resolveSessionTenant(fakeRedis(null, true), SID, "test")
    expect(falha.tenantId).toBeNull()
    expect(falha.reason).not.toBe(ausente.reason)
    expect(falha.reason).toContain("falha de leitura")
  })
})
