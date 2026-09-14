/**
 * lib/slot-promotion.ts — PID-16 (2026-09-14)
 *
 * A MECÂNICA do promote, numa casa só: mover `current → previous`, gravar o candidato em
 * `current`, limpar `next`, e registrar o `SkillDeployment`. O promote de um pool e o
 * promote em lote chamam as mesmas funções — duas cópias da troca de slots é como um
 * lote acaba deixando `previous` num estado que o promote simples não deixaria.
 */
import { prisma, Prisma } from "../db"

export interface SlotContent {
  skill_id:      string | null
  config_json:   unknown
  yaml_snapshot: unknown
}

/** Dentro de uma transação: `current → previous` (se houver), candidato → `current`, `next` limpo. */
// eslint-disable-next-line @typescript-eslint/no-explicit-any
export async function promoteSlotsInTx(tx: any, args: {
  tenantId:  string
  poolId:    string
  candidate: SlotContent
  current:   SlotContent | undefined
  userId:    string
  now:       Date
}): Promise<void> {
  const { tenantId, poolId, candidate, current, userId, now } = args

  if (current) {
    await tx.poolSkillSlot.upsert({
      where:  { pool_id_tenant_id_slot: { pool_id: poolId, tenant_id: tenantId, slot: "previous" } },
      update: {
        skill_id:      current.skill_id ?? null,
        config_json:   (current.config_json ?? {}) as Prisma.InputJsonValue,
        yaml_snapshot: (current.yaml_snapshot ?? Prisma.DbNull) as Prisma.InputJsonValue,
        set_at:        now,
        set_by:        userId,
      },
      create: {
        pool_id:       poolId,
        tenant_id:     tenantId,
        slot:          "previous",
        skill_id:      current.skill_id ?? null,
        config_json:   (current.config_json ?? {}) as Prisma.InputJsonValue,
        yaml_snapshot: (current.yaml_snapshot ?? Prisma.DbNull) as Prisma.InputJsonValue,
        set_by:        userId,
      },
    })
  }

  await tx.poolSkillSlot.upsert({
    where:  { pool_id_tenant_id_slot: { pool_id: poolId, tenant_id: tenantId, slot: "current" } },
    update: {
      skill_id:      candidate.skill_id ?? null,
      config_json:   (candidate.config_json ?? {}) as Prisma.InputJsonValue,
      yaml_snapshot: (candidate.yaml_snapshot ?? Prisma.DbNull) as Prisma.InputJsonValue,
      set_at:        now,
      set_by:        userId,
    },
    create: {
      pool_id:       poolId,
      tenant_id:     tenantId,
      slot:          "current",
      skill_id:      candidate.skill_id ?? null,
      config_json:   (candidate.config_json ?? {}) as Prisma.InputJsonValue,
      yaml_snapshot: (candidate.yaml_snapshot ?? Prisma.DbNull) as Prisma.InputJsonValue,
      set_by:        userId,
    },
  })

  await tx.poolSkillSlot.deleteMany({
    where: { pool_id: poolId, tenant_id: tenantId, slot: "next" },
  })
}

/**
 * Skill Versioning Fase C: o promote É o deploy → um `SkillDeployment` (append-log) com
 * `deployed_at = set_at`. A identidade da versão é o `now` (carimbado pelo bridge em
 * `segments.deploy_version`); `version` guarda o RÓTULO (`skill.version`).
 *
 * Não-fatal (o deploy já foi efetivado pelos slots), mas NUNCA mudo: desde a PID-08 o
 * promote é o ÚNICO escritor de `SkillDeployment`, então engolir a falha apagaria o
 * marker da lente e o rótulo do epoch sem rastro. Devolve se registrou.
 */
export async function recordSkillDeployment(args: {
  tenantId: string
  poolId:   string
  skillId:  string
  snapshot: unknown
  userId:   string
  now:      Date
  notes:    string
}): Promise<boolean> {
  const { tenantId, poolId, skillId, snapshot, userId, now, notes } = args
  if (!skillId) return false
  let versionLabel = ""
  try {
    const sk = await prisma.skill.findUnique({
      where: { skill_id_tenant_id: { skill_id: skillId, tenant_id: tenantId } },
    })
    versionLabel = ((sk as unknown as Record<string, unknown>)?.["version"] as string) || ""
  } catch { /* sem rótulo → epoch cai para a data do deploy */ }
  try {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    await (prisma as any).skillDeployment.create({
      data: {
        skill_id:      skillId,
        tenant_id:     tenantId,
        version:       versionLabel,
        pool_ids:      [poolId],
        yaml_snapshot: (snapshot ?? null) as Prisma.InputJsonValue,
        deployed_by:   userId,
        deployed_at:   now,
        notes,
      },
    })
    return true
  } catch (err) {
    console.error(
      `[pool-slots:${notes}] SkillDeployment NÃO registrado — pool=${poolId} skill=${skillId}: ` +
      `${err instanceof Error ? err.message : String(err)}. O slot foi promovido; a lente de deploy fica sem este marker.`,
    )
    return false
  }
}
