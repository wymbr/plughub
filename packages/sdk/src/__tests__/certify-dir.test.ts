/**
 * certify-dir.test.ts
 * Testa certifyDir com fixtures de repositórios GitAgent.
 */

import { describe, it, expect } from "vitest"
import * as path from "path"
import { certifyDir } from "../certify/dir"

const FIXTURES = path.join(__dirname, "fixtures")

describe("certifyDir — gitagent-base (sem flow.yaml)", () => {
  it("certifica com sucesso", () => {
    const report = certifyDir(path.join(FIXTURES, "gitagent-base"))
    expect(report.status).toBe("certified")
    expect(report.agent_type_id).toBe("agente_suporte_v1")
  })

  it("manifesto válido — agent_type_id, framework, execution_model", () => {
    const report = certifyDir(path.join(FIXTURES, "gitagent-base"))
    const check  = report.checks.find(c => c.name === "manifest.fields")
    expect(check?.status).toBe("passed")
  })

  it("todos os 6 eventos de ciclo de vida presentes", () => {
    const report = certifyDir(path.join(FIXTURES, "gitagent-base"))
    const check  = report.checks.find(c => c.name === "lifecycle.events")
    expect(check?.status).toBe("passed")
  })

  it("issue_status presente no agent_done", () => {
    const report = certifyDir(path.join(FIXTURES, "gitagent-base"))
    const check  = report.checks.find(c => c.name === "contract.issue_status")
    expect(check?.status).toBe("passed")
  })

  it("sem check de flow (flow.yaml não existe)", () => {
    const report = certifyDir(path.join(FIXTURES, "gitagent-base"))
    const flowCheck = report.checks.find(c => c.name === "flow.valid")
    // flow é opcional — não deve aparecer quando ausente
    expect(flowCheck).toBeUndefined()
  })
})

describe("certifyDir — gitagent-with-flow (flow.yaml válido)", () => {
  it("certifica com sucesso", () => {
    const report = certifyDir(path.join(FIXTURES, "gitagent-with-flow"))
    expect(report.status).toBe("certified")
  })

  it("flow.yaml válido — check passed", () => {
    const report    = certifyDir(path.join(FIXTURES, "gitagent-with-flow"))
    const flowCheck = report.checks.find(c => c.name === "flow.valid")
    expect(flowCheck).toBeDefined()
    expect(flowCheck!.status).toBe("passed")
  })

  it("flow.yaml reporta entry e contagem de steps", () => {
    const report    = certifyDir(path.join(FIXTURES, "gitagent-with-flow"))
    const flowCheck = report.checks.find(c => c.name === "flow.valid")
    expect(flowCheck!.message).toMatch(/check_customer/)
    expect(flowCheck!.message).toMatch(/3 steps/)
  })
})

describe("certifyDir — gitagent-broken-flow (flow.yaml com next quebrado)", () => {
  it("reprova a certificação", () => {
    const report = certifyDir(path.join(FIXTURES, "gitagent-broken-flow"))
    expect(report.status).toBe("failed")
  })

  it("flow.valid — check failed com detalhes do erro", () => {
    const report    = certifyDir(path.join(FIXTURES, "gitagent-broken-flow"))
    const flowCheck = report.checks.find(c => c.name === "flow.valid")
    expect(flowCheck?.status).toBe("failed")
    expect(flowCheck?.errors?.some(e => e.message.includes("nonexistent_step"))).toBe(true)
  })
})

describe("certifyDir — gitagent-no-issue-status (agent_done sem issue_status)", () => {
  it("reprova a certificação", () => {
    const report = certifyDir(path.join(FIXTURES, "gitagent-no-issue-status"))
    expect(report.status).toBe("failed")
  })

  it("contract.issue_status — check failed", () => {
    const report = certifyDir(path.join(FIXTURES, "gitagent-no-issue-status"))
    const check  = report.checks.find(c => c.name === "contract.issue_status")
    expect(check?.status).toBe("failed")
    expect(check?.errors?.length).toBeGreaterThan(0)
  })
})

// ─────────────────────────────────────────────
// Proxy sidecar checks (spec 4.6k)
// ─────────────────────────────────────────────

describe("certifyDir — agente externo sem proxy_config.yaml deve reprovar", () => {
  it("reprova a certificação", () => {
    const report = certifyDir(path.join(FIXTURES, "gitagent-external-no-proxy"))
    expect(report.status).toBe("failed")
  })

  it("proxy.config_present — check failed com mensagem correta", () => {
    const report = certifyDir(path.join(FIXTURES, "gitagent-external-no-proxy"))
    const check  = report.checks.find(c => c.name === "proxy.config_present")
    expect(check?.status).toBe("failed")
    expect(check?.message).toMatch(/proxy_config\.yaml/)
  })
})

describe("certifyDir — agente nativo sem proxy_config.yaml deve passar", () => {
  it("certifica com sucesso (native não precisa de proxy)", () => {
    const report = certifyDir(path.join(FIXTURES, "gitagent-native"))
    expect(report.status).toBe("certified")
  })

  it("não inclui check de proxy para agentes nativos", () => {
    const report = certifyDir(path.join(FIXTURES, "gitagent-native"))
    const check  = report.checks.find(c => c.name === "proxy.config_present")
    expect(check).toBeUndefined()
  })
})
