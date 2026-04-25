-- AlterTable: add queue_config column to pools
-- PlugHub Queue Agent Pattern — spec section Queue Agent
ALTER TABLE "pools" ADD COLUMN "queue_config" JSONB;
