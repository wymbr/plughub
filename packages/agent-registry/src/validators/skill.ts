/**
 * validators/skill.ts
 * Validação Zod dos payloads de skill.
 */

import { z } from "zod"
import { SkillSchema } from "@plughub/schemas"

export const CreateSkillSchema = SkillSchema
// SkillSchema is ZodEffects (has .refine). Access the inner ZodObject for partial operations.
const _SkillBase = (SkillSchema as unknown as { _def: { schema: z.ZodObject<z.ZodRawShape> } })._def.schema
export const UpdateSkillSchema = _SkillBase.partial().omit({ skill_id: true })
