/**
 * lib/transport-credential.ts — CAP-10 (2026-09-29)
 *
 * O transporte MCP (`GET /sse` + `POST /messages`) passa a exigir credencial de SERVIÇO.
 *
 * Até aqui ele era anônimo por construção, e a defesa era só de topologia: a 3100 publica
 * em loopback (CAP-13), então fora do host ninguém a alcançava. A v1 de produção roda em
 * instância dedicada com réplicas (`producao-v1.md`, PRD-01/PRD-06) — seja Kubernetes, seja
 * compose multi-host, o mcp-server passa a ser alcançado PELA REDE por quem chama as tools,
 * que é o gatilho que a própria ficha nomeou. E o que a topologia escondia não era pouco:
 * `agent_login` é AUTO-SERVIÇO, então quem alcança a porta cunha um `session_token`
 * assinado nomeando qualquer skill, e as tools que conferem esse token aceitam um token
 * que qualquer um emite. Fechar tool a tool não resolve isso; fechar o transporte sim.
 *
 * A credencial é a mesma das `/internal/*` (`MCP_INTERNAL_SERVICE_TOKEN`), e o modo de
 * falha é o delas: env vazio RECUSA (503), nunca abre. Token vazio no chamador não passa.
 *
 * Pura, para que a decisão seja testada sem subir o servidor.
 */
import crypto from "crypto"

export type TransportVerdict = "ok" | "not_configured" | "refused"

export function judgeTransportCredential(expected: string | undefined, got: string | undefined): TransportVerdict {
  const esperado = expected ?? ""
  if (!esperado) return "not_configured"
  const recebido = got ?? ""
  if (recebido.length !== esperado.length) return "refused"
  return crypto.timingSafeEqual(Buffer.from(recebido), Buffer.from(esperado)) ? "ok" : "refused"
}

/** O header que o transporte confere — o mesmo nome das `/internal/*`. */
export const TRANSPORT_CREDENTIAL_HEADER = "x-service-token"
