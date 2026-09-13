import { describe, it, expect } from "vitest"
import { injectSessionToken, isSessionBoundTool, SESSION_BOUND_TOOLS } from "./session-bound-tools"

describe("PID-01 — injeção do token de sessão", () => {
  it("as duas tools de retomada estão na lista", () => {
    expect([...SESSION_BOUND_TOOLS].sort()).toEqual(["pending_workflow_get", "workflow_resume"])
  })

  it("injeta na tool gateada do mcp-server-plughub", () => {
    expect(injectSessionToken("workflow_resume", { resume_token: "r" }, "TOK")).toEqual({ resume_token: "r", session_token: "TOK" })
    expect(injectSessionToken("pending_workflow_get", { anchors: [] }, "TOK", "mcp-server-plughub"))
      .toEqual({ anchors: [], session_token: "TOK" })
  })

  it("SOBRESCREVE o token que o YAML trouxer — o autor não escolhe a sessão", () => {
    expect(injectSessionToken("workflow_resume", { session_token: "DO_YAML" }, "TOK")).toEqual({ session_token: "TOK" })
  })

  it("sem token emitido, REMOVE o do YAML (a tool recusa nomeando)", () => {
    expect(injectSessionToken("workflow_resume", { resume_token: "r", session_token: "DO_YAML" }, undefined))
      .toEqual({ resume_token: "r" })
  })

  it("controle: tool fora da lista e servidor externo ficam intactos — inclusive o session_token do agent_login", () => {
    const input = { session_token: "AGENTE", x: 1 }
    expect(injectSessionToken("journey_merge", input, "TOK")).toBe(input)
    expect(injectSessionToken("workflow_resume", input, "TOK", "mcp-server-crm")).toBe(input)
    expect(isSessionBoundTool("evaluation_submit")).toBe(false)
  })
})
