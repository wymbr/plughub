/**
 * pool-registered.ts — AGH-04 (2026-09-21): o login humano PERGUNTA se o pool existe; não o cria.
 *
 * Até aqui o login (`registerHumanAgent`, Step 0) fazia `POST /v1/pools` com config fixa no código
 * (`channel_types`, `sla_target_ms`) "para garantir que o pool existe". Medido: desde o restart o POST
 * criou ZERO pools — o Console só oferece pools vindos do registry (`/v1/pools`), e os cinco probes
 * que abrem o WS provisionam o pool pela API antes. Ele só servia para (a) um WARN em TODO login num
 * espelho `-int`, porque o registry valida o sufixo reservado antes de conferir existência (422, não
 * 409), e (b) uma porta lateral de provisionamento: abrir o WS com um `pool_id` inventado criava pool
 * com config hardcoded, contra "provisioning only via official API".
 *
 * Três desfechos, cada um com o seu destino no chamador:
 *   registered — 200: segue.
 *   absent     — 404: RECUSA o login (`pool_not_registered`). Pool que o registry não conhece tem o
 *                `pool_config` apagado pelo reconciliador do bridge: logar ali é logar num fantasma.
 *   unverified — registry fora / outro status: SEGUE, e o chamador loga o `why`. Recusar login por
 *                falha de infra tiraria o atendimento do ar pela dependência errada (mesma postura do
 *                gate de kind, logo acima no login).
 */
export type PoolRegistration =
  | { kind: "registered" }
  | { kind: "absent" }
  | { kind: "unverified"; why: string }

export async function checkPoolRegistered(
  registryUrl: string,
  tenantId: string,
  poolId: string,
  serviceToken: string,
  fetchImpl: typeof fetch = fetch,
): Promise<PoolRegistration> {
  try {
    const resp = await fetchImpl(`${registryUrl}/v1/pools/${encodeURIComponent(poolId)}`, {
      method: "GET",
      headers: {
        "x-tenant-id": tenantId,
        ...(serviceToken ? { "x-service-token": serviceToken } : {}),
      },
    })
    if (resp.ok) return { kind: "registered" }
    if (resp.status === 404) return { kind: "absent" }
    let detail = ""
    try { detail = (await resp.text()).slice(0, 200) } catch { /* corpo ilegível */ }
    return { kind: "unverified", why: `registry respondeu HTTP ${resp.status}: ${detail}` }
  } catch (err) {
    return { kind: "unverified", why: `registry inalcançável: ${String(err)}` }
  }
}
