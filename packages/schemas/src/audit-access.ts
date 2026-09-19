/**
 * audit-access.ts
 * Fato de ACESSO A DADO PESSOAL servido fora da analytics-api (VOZ-36).
 *
 * Tópico Kafka: audit.access — produtor channel-gateway (`recording_router.py`: ouvir e exportar
 * gravação de chamada), consumidor analytics-api, que grava em `audit_access_log`. A trilha LGPD
 * tem UMA escritora (a analytics-api); quem serve dado pessoal noutro serviço manda o fato por
 * aqui em vez de escrever no ClickHouse. Chave de partição = `tenant_id`.
 *
 * A recusa é fato tão relevante quanto o acesso: `result: "denied"` inclui a chamada SEM
 * credencial (`actor_kind: "anonymous"`, `actor_sub: ""`). `row_count` é 1 no acesso concedido
 * e 0 na recusa.
 *
 * ⚠️ `.strict()`: o evento descreve QUEM acessou O QUÊ, nunca o conteúdo. Um campo novo reprova
 * a validação em vez de levar dado pessoal à trilha por engano.
 */

import { z } from "zod"

export const AUDIT_ACCESS_RESULTS = ["ok", "denied"] as const
export const AUDIT_ACCESS_ACTOR_KINDS = ["user", "anonymous", "service"] as const

export const AuditAccessEventSchema = z
  .object({
    event_id:    z.string().uuid(),
    tenant_id:   z.string().min(1),
    actor_sub:   z.string(),
    actor_kind:  z.enum(AUDIT_ACCESS_ACTOR_KINDS),
    /** `<serviço>:<ação>` — ex.: `channel-gateway:recording.listen` */
    endpoint:    z.string().min(1),
    /** ex.: `recording` */
    target_kind: z.string().min(1),
    /** ex.: `{session_id}/{file_id}`; o file_id cru quando o alvo não chegou a ser resolvido */
    target_id:   z.string().min(1),
    result:      z.enum(AUDIT_ACCESS_RESULTS),
    row_count:   z.number().int().min(0),
    accessed_at: z.string().datetime({ offset: true }),
  })
  .strict()

export type AuditAccessEvent = z.infer<typeof AuditAccessEventSchema>
