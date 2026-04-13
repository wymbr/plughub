/**
 * regenerate.test.ts
 * Testa readGitAgent e convertGitAgent com fixtures.
 */

import { describe, it, expect, afterEach } from "vitest"
import * as path from "path"
import * as fs   from "fs"
import * as os   from "os"
import { readGitAgent }    from "../regenerate/reader"
import { convertGitAgent } from "../regenerate/convert"

const FIXTURES = path.join(__dirname, "fixtures")

// Diretório temporário por teste
const tmpDirs: string[] = []
function makeTmp(): string {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "plughub-regen-"))
  tmpDirs.push(dir)
  return dir
}
afterEach(() => {
  for (const d of tmpDirs.splice(0)) {
    try { fs.rmSync(d, { recursive: true, force: true }) } catch { /* ignore */ }
  }
})

// ─────────────────────────────────────────────
// readGitAgent
// ─────────────────────────────────────────────

describe("readGitAgent — gitagent-base", () => {
  it("lê manifesto corretamente", () => {
    const a = readGitAgent(path.join(FIXTURES, "gitagent-base"))
    expect(a.manifest?.agent_type_id).toBe("agente_suporte_v1")
    expect(a.manifest?.framework).toBe("proprietary")
    expect(a.manifest?.execution_model).toBe("stateless")
    expect(a.manifest?.pools).toContain("suporte_humano")
  })

  it("lê SOUL.md e DUTIES.md", () => {
    const a = readGitAgent(path.join(FIXTURES, "gitagent-base"))
    expect(a.soul).toBeDefined()
    expect(a.duties).toBeDefined()
  })

  it("sem flow — flow e flowErrors undefined", () => {
    const a = readGitAgent(path.join(FIXTURES, "gitagent-base"))
    expect(a.flow).toBeUndefined()
    expect(a.flowErrors).toBeUndefined()
  })

  it("version fallback para '1.0.0' (sem git tag)", () => {
    const a = readGitAgent(path.join(FIXTURES, "gitagent-base"))
    // versão vem do manifesto (field version: "1.0.0")
    expect(a.version).toBe("1.0.0")
  })
})

describe("readGitAgent — gitagent-with-flow", () => {
  it("lê flow válido como ParsedFlow", () => {
    const a = readGitAgent(path.join(FIXTURES, "gitagent-with-flow"))
    expect(a.flow).toBeDefined()
    expect(a.flow?.entry).toBe("check_customer")
    expect(a.flow?.steps).toHaveLength(3)
  })

  it("flowErrors undefined quando flow é válido", () => {
    const a = readGitAgent(path.join(FIXTURES, "gitagent-with-flow"))
    expect(a.flowErrors).toBeUndefined()
  })
})

describe("readGitAgent — gitagent-broken-flow", () => {
  it("flow undefined e flowErrors preenchido quando flow.yaml inválido", () => {
    const a = readGitAgent(path.join(FIXTURES, "gitagent-broken-flow"))
    expect(a.flow).toBeUndefined()
    expect(a.flowErrors).toBeDefined()
    expect(a.flowErrors!.length).toBeGreaterThan(0)
  })

  it("flowErrors menciona a referência quebrada", () => {
    const a = readGitAgent(path.join(FIXTURES, "gitagent-broken-flow"))
    const msgs = a.flowErrors!.map(e => e.message).join(" ")
    expect(msgs).toMatch(/nonexistent_step/)
  })
})

// ─────────────────────────────────────────────
// convertGitAgent
// ─────────────────────────────────────────────

describe("convertGitAgent — gitagent-base", () => {
  it("gera agent-type.json e prompt.md", () => {
    const tmp = makeTmp()
    const a   = readGitAgent(path.join(FIXTURES, "gitagent-base"))
    const r   = convertGitAgent(a, tmp)
    expect(r.files).toContain("agent-type.json")
    expect(r.files).toContain("prompt.md")
  })

  it("não gera flow.json (sem flow.yaml)", () => {
    const tmp = makeTmp()
    const a   = readGitAgent(path.join(FIXTURES, "gitagent-base"))
    const r   = convertGitAgent(a, tmp)
    expect(r.files).not.toContain("flow.json")
  })

  it("agent-type.json contém campos corretos", () => {
    const tmp = makeTmp()
    const a   = readGitAgent(path.join(FIXTURES, "gitagent-base"))
    convertGitAgent(a, tmp)
    const at  = JSON.parse(fs.readFileSync(path.join(tmp, "agent-type.json"), "utf-8"))
    expect(at.agent_type_id).toBe("agente_suporte_v1")
    expect(at.framework).toBe("proprietary")
    expect(at.execution_model).toBe("stateless")
    expect(at.pools).toContain("suporte_humano")
    expect(at._generated_from).toBe("plughub-sdk regenerate")
  })

  it("prompt.md contém SOUL.md e DUTIES.md", () => {
    const tmp = makeTmp()
    const a   = readGitAgent(path.join(FIXTURES, "gitagent-base"))
    convertGitAgent(a, tmp)
    const md  = fs.readFileSync(path.join(tmp, "prompt.md"), "utf-8")
    expect(md).toMatch(/Camada 1/)
    expect(md).toMatch(/Camada 2/)
    expect(md).toMatch(/suporte especializado/)
  })

  it("sem avisos quando SOUL.md e DUTIES.md presentes", () => {
    const tmp = makeTmp()
    const a   = readGitAgent(path.join(FIXTURES, "gitagent-base"))
    const r   = convertGitAgent(a, tmp)
    expect(r.warnings).toHaveLength(0)
  })
})

describe("convertGitAgent — gitagent-with-flow", () => {
  it("gera flow.json quando flow.yaml é válido", () => {
    const tmp = makeTmp()
    const a   = readGitAgent(path.join(FIXTURES, "gitagent-with-flow"))
    const r   = convertGitAgent(a, tmp)
    expect(r.files).toContain("flow.json")
  })

  it("flow.json preserva entry e steps", () => {
    const tmp = makeTmp()
    const a   = readGitAgent(path.join(FIXTURES, "gitagent-with-flow"))
    convertGitAgent(a, tmp)
    const flow = JSON.parse(fs.readFileSync(path.join(tmp, "flow.json"), "utf-8"))
    expect(flow.entry).toBe("check_customer")
    expect(flow.steps).toHaveLength(3)
  })

  it("step task converte agent_pool para target.skill_id", () => {
    const tmp = makeTmp()
    const a   = readGitAgent(path.join(FIXTURES, "gitagent-with-flow"))
    convertGitAgent(a, tmp)
    const flow  = JSON.parse(fs.readFileSync(path.join(tmp, "flow.json"), "utf-8"))
    const task  = flow.steps.find((s: { id: string }) => s.id === "check_customer")
    expect(task?.target?.skill_id).toBeDefined()
    expect(task?._agent_pool).toBe("suporte_humano")
  })
})

describe("convertGitAgent — gitagent-broken-flow", () => {
  it("lança erro (regenerate abortado) quando flow.yaml é inválido", () => {
    const tmp = makeTmp()
    const a   = readGitAgent(path.join(FIXTURES, "gitagent-broken-flow"))
    expect(() => convertGitAgent(a, tmp)).toThrow(/flow.yaml inválido/)
  })

  it("erro menciona o step com referência quebrada", () => {
    const tmp = makeTmp()
    const a   = readGitAgent(path.join(FIXTURES, "gitagent-broken-flow"))
    let msg = ""
    try { convertGitAgent(a, tmp) } catch (e) { msg = String(e) }
    expect(msg).toMatch(/nonexistent_step/)
  })
})

// ─────────────────────────────────────────────
// proxy_config.yaml generation
// ─────────────────────────────────────────────

describe("convertGitAgent — proxy_config.yaml gerado quando permissions[] declara MCP Servers", () => {
  it("gera proxy_config.yaml com 2 entradas em routes[]", () => {
    const tmp = makeTmp()
    const a   = readGitAgent(path.join(FIXTURES, "gitagent-with-mcp-permissions"))
    const r   = convertGitAgent(a, tmp)
    expect(r.files).toContain("proxy_config.yaml")
    const content = fs.readFileSync(path.join(tmp, "proxy_config.yaml"), "utf-8")
    expect(content).toMatch(/mcp-server-crm/)
    expect(content).toMatch(/mcp-server-telco/)
    expect(content).toMatch(/MCP_CRM_URL/)
    expect(content).toMatch(/MCP_TELCO_URL/)
  })

  it("proxy_config.yaml contém port 7422 e circuit_breaker", () => {
    const tmp = makeTmp()
    const a   = readGitAgent(path.join(FIXTURES, "gitagent-with-mcp-permissions"))
    convertGitAgent(a, tmp)
    const content = fs.readFileSync(path.join(tmp, "proxy_config.yaml"), "utf-8")
    expect(content).toMatch(/port: 7422/)
    expect(content).toMatch(/mode_on_failure: error_clear/)
  })
})

describe("convertGitAgent — proxy_config.yaml NÃO gerado quando permissions[] vazio", () => {
  it("não gera proxy_config.yaml", () => {
    const tmp = makeTmp()
    const a   = readGitAgent(path.join(FIXTURES, "gitagent-empty-permissions"))
    const r   = convertGitAgent(a, tmp)
    expect(r.files).not.toContain("proxy_config.yaml")
    expect(fs.existsSync(path.join(tmp, "proxy_config.yaml"))).toBe(false)
  })
})
