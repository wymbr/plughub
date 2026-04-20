/**
 * validators/canary.ts
 * Validação do payload de ajuste de canário.
 * Spec: PlugHub v24.0 seção 4.5 — progressão de traffic_weight
 */

import { z } from "zod"

/** Ajuste manual de traffic_weight para progressão ou rollback parcial. */
export const CanaryPatchSchema = z.object({
  traffic_weight: z
    .number()
    .min(0.0, "traffic_weight mínimo é 0.0")
    .max(1.0, "traffic_weight máximo é 1.0"),
})
