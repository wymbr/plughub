/**
 * validators/pool.ts
 * Validação Zod dos payloads de pool.
 * Reutiliza PoolRegistrationSchema de @plughub/schemas.
 */

import { PoolRegistrationSchema } from "@plughub/schemas"

export const CreatePoolSchema = PoolRegistrationSchema.extend({})
export const UpdatePoolSchema = PoolRegistrationSchema.partial().omit({ pool_id: true })
