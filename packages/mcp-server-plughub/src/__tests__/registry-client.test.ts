/**
 * registry-client.test.ts
 * `tools[]` da skill → `permissions[]` do session_token (CAP-06, 2026-09-01).
 *
 * O DEFEITO QUE ESTES TESTES EXISTEM PARA IMPEDIR
 * ==============================================
 * Até 2026-09-01 o `getAgentType` devolvia `permissions: []` FIXO, com um
 * comentário ao lado afirmando que vazio era "the permissive default (⇒ no MCP
 * tool filtering)". Era falso na borda que importa: `judgeInvoke([], …)` devolve
 * `permission_denied` — 100% das chamadas —, tornando INTRANSITÁVEL a única borda
 * MCP que o `CLAUDE.md` declara em vigor. Não doía porque a população era zero.
 *
 * O QUE FARIA ESTES TESTES FICAREM VERMELHOS (a pergunta que vale)
 * ===============================================================
 *   - voltar a fixar `[]` (o teste do caminho feliz cai);
 *   - filtrar por `required` (o ramo da tool opcional cai) — `required` responde
 *     "a skill funciona sem ela?", que é dependência, não autorização;
 *   - emitir `"undefined:foo"` para entrada malformada (o ramo de descarte cai) —
 *     seria uma permissão que não casa com nada, isto é, recusa silenciosa
 *     disfarçada de concessão;
 *   - emitir curinga `"{server}:*"` — vale no sidecar e NÃO vale no `judgeInvoke`.
 *
 * O par com `judgeInvoke` é asserção deste arquivo, não suposição: o último bloco
 * alimenta o veredicto real com o produto desta função. Sem ele, os dois lados
 * poderiam divergir de formato e as duas suítes continuariam verdes.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { toPermissions, createRegistryClient } from "../infra/registry-client.js"
import { judgeInvoke } from "../lib/invoke-audit.js"

describe("toPermissions — tools[] do registry → permissions[] do token", () => {
  it("mapeia para o formato exato que o judgeInvoke exige", () => {
    expect(
      toPermissions([
        { mcp_server: "mcp-server-crm", tool: "customer_get", required: true },
        { mcp_server: "mcp-server-auth", tool: "validate_pin", required: true },
      ])
    ).toEqual(["mcp-server-crm:customer_get", "mcp-server-auth:validate_pin"])
  })

  it("NÃO filtra por `required` — dependência não é autorização", () => {
    const out = toPermissions([
      { mcp_server: "mcp-server-crm", tool: "customer_get", required: false },
    ])
    expect(out).toEqual(["mcp-server-crm:customer_get"])
  })

  it("tolera `required` ausente (o schema tem default, o JSON do registry pode não trazer)", () => {
    expect(toPermissions([{ mcp_server: "s", tool: "t" }])).toEqual(["s:t"])
  })

  it("deduplica preservando a ordem — a lista viaja no JWT e é ecoada na recusa", () => {
    expect(
      toPermissions([
        { mcp_server: "a", tool: "x" },
        { mcp_server: "b", tool: "y" },
        { mcp_server: "a", tool: "x" },
      ])
    ).toEqual(["a:x", "b:y"])
  })

  describe("entrada malformada é DESCARTADA COM LOG, nunca convertida", () => {
    let warn: ReturnType<typeof vi.spyOn>

    beforeEach(() => { warn = vi.spyOn(console, "warn").mockImplementation(() => {}) })
    afterEach(()  => { warn.mockRestore() })

    it.each([
      ["mcp_server ausente", { tool: "customer_get" }],
      ["tool ausente",       { mcp_server: "mcp-server-crm" }],
      ["mcp_server vazio",   { mcp_server: "   ", tool: "customer_get" }],
      ["tool vazia",         { mcp_server: "mcp-server-crm", tool: "" }],
      ["tipo errado",        { mcp_server: 42, tool: ["customer_get"] }],
      ["não é objeto",       "mcp-server-crm:customer_get"],
      ["nulo",               null],
    ])("descarta e loga: %s", (_label, entry) => {
      expect(toPermissions([entry])).toEqual([])
      expect(warn).toHaveBeenCalledTimes(1)
      expect(String(warn.mock.calls[0]?.[0])).toContain("DESCARTADA")
    })

    it("descarta só a entrada ruim — a boa da mesma lista sobrevive", () => {
      expect(
        toPermissions([{ mcp_server: "a" }, { mcp_server: "b", tool: "y" }])
      ).toEqual(["b:y"])
      expect(warn).toHaveBeenCalledTimes(1)
    })
  })

  it.each([
    ["ausente",   undefined],
    ["nulo",      null],
    ["não-array", { mcp_server: "a", tool: "x" }],
    ["vazio",     []],
  ])("devolve [] para tools %s (skill sem declaração continua como estava)", (_l, raw) => {
    expect(toPermissions(raw)).toEqual([])
  })
})

describe("createRegistryClient.getAgentType — o elo que faltava", () => {
  const mkRes = (body: unknown, status = 200) => ({
    ok:     status >= 200 && status < 300,
    status,
    json:   async () => body,
  }) as Response

  afterEach(() => { vi.unstubAllGlobals() })

  it("assina o tools[] declarado na skill (antes era [] FIXO)", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => mkRes({
      skill_id: "skill_externo_v1",
      tools:    [{ mcp_server: "mcp-server-crm", tool: "customer_get", required: true }],
    })))

    const info = await createRegistryClient("http://reg").getAgentType("t", "skill_externo_v1")
    expect(info?.permissions).toEqual(["mcp-server-crm:customer_get"])
  })

  it("skill sem tools continua com [] — a virada é OPT-IN por declaração", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => mkRes({ skill_id: "skill_sem_tools_v1" })))

    const info = await createRegistryClient("http://reg").getAgentType("t", "skill_sem_tools_v1")
    expect(info?.permissions).toEqual([])
  })

  it("404 continua devolvendo null — agent_login recusa o login, não o degrada", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => mkRes({ error: "não encontrada" }, 404)))

    expect(await createRegistryClient("http://reg").getAgentType("t", "nao_existe")).toBeNull()
  })
})

describe("acoplamento com judgeInvoke — o formato tem de casar de verdade", () => {
  const permissions = toPermissions([
    { mcp_server: "mcp-server-crm", tool: "customer_get", required: true },
  ])

  it("a tool DECLARADA passa", () => {
    expect(judgeInvoke(permissions, "mcp-server-crm", "customer_get", {}))
      .toEqual({ allowed: true })
  })

  it("a tool NÃO declarada é negada — testemunha negativa", () => {
    const v = judgeInvoke(permissions, "mcp-server-crm", "customer_delete", {})
    expect(v.allowed).toBe(false)
    expect(v).toMatchObject({ reason: "permission_denied" })
  })

  it("o MESMO servidor com outra tool não vaza por curinga implícito", () => {
    expect(judgeInvoke(permissions, "mcp-server-outro", "customer_get", {}).allowed).toBe(false)
  })

  it("CAP-05: a lista VAZIA nega tudo — é isto que o default fixo causava", () => {
    expect(judgeInvoke([], "mcp-server-crm", "customer_get", {}).allowed).toBe(false)
  })
})
