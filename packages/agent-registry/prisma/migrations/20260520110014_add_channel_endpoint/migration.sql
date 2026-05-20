/*
  Warnings:

  - The primary key for the `pool_skill_slots` table will be changed. If it partially fails, the table could be left without primary key constraint.
  - The primary key for the `skill_deployments` table will be changed. If it partially fails, the table could be left without primary key constraint.
  - The primary key for the `skill_version_slots` table will be changed. If it partially fails, the table could be left without primary key constraint.

*/
-- DropForeignKey
ALTER TABLE "pool_skill_slots" DROP CONSTRAINT "fk_pool_skill_slot_pool";

-- DropForeignKey
ALTER TABLE "skill_deployments" DROP CONSTRAINT "fk_skill_deployment";

-- DropForeignKey
ALTER TABLE "skill_version_slots" DROP CONSTRAINT "fk_skill_version_slot";

-- AlterTable
ALTER TABLE "channel_endpoints" ALTER COLUMN "id" DROP DEFAULT,
ALTER COLUMN "created_at" SET DATA TYPE TIMESTAMP(3),
ALTER COLUMN "updated_at" DROP DEFAULT,
ALTER COLUMN "updated_at" SET DATA TYPE TIMESTAMP(3);

-- AlterTable
ALTER TABLE "gateway_configs" ALTER COLUMN "id" DROP DEFAULT,
ALTER COLUMN "created_at" SET DATA TYPE TIMESTAMP(3),
ALTER COLUMN "updated_at" DROP DEFAULT,
ALTER COLUMN "updated_at" SET DATA TYPE TIMESTAMP(3);

-- AlterTable
ALTER TABLE "pool_skill_slots" DROP CONSTRAINT "pool_skill_slots_pkey",
ALTER COLUMN "id" DROP DEFAULT,
ALTER COLUMN "id" SET DATA TYPE TEXT,
ALTER COLUMN "set_at" SET DATA TYPE TIMESTAMP(3),
ADD CONSTRAINT "pool_skill_slots_pkey" PRIMARY KEY ("id");

-- AlterTable
ALTER TABLE "pools" ADD COLUMN     "agent_groups" TEXT[] DEFAULT ARRAY[]::TEXT[],
ADD COLUMN     "mentionable_journeys" JSONB;

-- AlterTable
ALTER TABLE "skill_deployments" DROP CONSTRAINT "skill_deployments_pkey",
ALTER COLUMN "id" DROP DEFAULT,
ALTER COLUMN "id" SET DATA TYPE TEXT,
ALTER COLUMN "pool_ids" DROP DEFAULT,
ALTER COLUMN "deployed_at" SET DATA TYPE TIMESTAMP(3),
ADD CONSTRAINT "skill_deployments_pkey" PRIMARY KEY ("id");

-- AlterTable
ALTER TABLE "skill_version_slots" DROP CONSTRAINT "skill_version_slots_pkey",
ALTER COLUMN "id" DROP DEFAULT,
ALTER COLUMN "id" SET DATA TYPE TEXT,
ALTER COLUMN "set_at" SET DATA TYPE TIMESTAMP(3),
ADD CONSTRAINT "skill_version_slots_pkey" PRIMARY KEY ("id");

-- AlterTable
ALTER TABLE "skills" ALTER COLUMN "published_at" SET DATA TYPE TIMESTAMP(3);

-- AddForeignKey
ALTER TABLE "skill_deployments" ADD CONSTRAINT "skill_deployments_skill_id_tenant_id_fkey" FOREIGN KEY ("skill_id", "tenant_id") REFERENCES "skills"("skill_id", "tenant_id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "skill_version_slots" ADD CONSTRAINT "skill_version_slots_skill_id_tenant_id_fkey" FOREIGN KEY ("skill_id", "tenant_id") REFERENCES "skills"("skill_id", "tenant_id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "pool_skill_slots" ADD CONSTRAINT "pool_skill_slots_pool_id_tenant_id_fkey" FOREIGN KEY ("pool_id", "tenant_id") REFERENCES "pools"("pool_id", "tenant_id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "channel_endpoints" ADD CONSTRAINT "channel_endpoints_gateway_config_id_fkey" FOREIGN KEY ("gateway_config_id") REFERENCES "gateway_configs"("id") ON DELETE SET NULL ON UPDATE CASCADE;

-- RenameIndex
ALTER INDEX "channel_endpoints_tenant_channel_identifier_key" RENAME TO "channel_endpoints_tenant_id_channel_identifier_key";

-- RenameIndex
ALTER INDEX "pool_skill_slots_pool_tenant_idx" RENAME TO "pool_skill_slots_pool_id_tenant_id_idx";

-- RenameIndex
ALTER INDEX "uq_pool_skill_slot" RENAME TO "pool_skill_slots_pool_id_tenant_id_slot_key";

-- RenameIndex
ALTER INDEX "skill_deployments_skill_tenant_idx" RENAME TO "skill_deployments_skill_id_tenant_id_idx";

-- RenameIndex
ALTER INDEX "skill_deployments_tenant_idx" RENAME TO "skill_deployments_tenant_id_idx";

-- RenameIndex
ALTER INDEX "skill_version_slots_skill_tenant_idx" RENAME TO "skill_version_slots_skill_id_tenant_id_idx";

-- RenameIndex
ALTER INDEX "uq_skill_version_slot" RENAME TO "skill_version_slots_skill_id_tenant_id_slot_key";

-- RenameIndex
ALTER INDEX "skills_tenant_deploy_status_idx" RENAME TO "skills_tenant_id_deploy_status_idx";
