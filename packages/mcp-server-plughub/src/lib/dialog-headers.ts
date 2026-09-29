/**
 * dialog-headers.ts — AUT-62: os headers com que o mcp-server LÊ a dialog-api.
 *
 * A dialog-api tinha leitura aberta e a borda pública a publicava: com um `X-Tenant-ID`
 * qualquer um lia os formulários de todos os tenants. Desde 2026-09-29 o runtime entra por
 * `X-Service-Token` (só leitura). Os quatro leitores daqui — `form_get` (duas rotas), o
 * survey e a captura do segmento — montam o header AQUI, uma casa só.
 *
 * O token é lido a cada chamada (não capturado no import), e vazio ⇒ o header sai sem ele,
 * a dialog-api responde 401 e cada tool devolve o erro que já devolvia. Nunca "funciona sem
 * credencial".
 */
export function dialogHeaders(tenantId: string): Record<string, string> {
  const token = process.env["DIALOG_SERVICE_TOKEN"] ?? ""
  return token
    ? { "X-Tenant-ID": tenantId, "X-Service-Token": token }
    : { "X-Tenant-ID": tenantId }
}
