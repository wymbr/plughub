-- CreateEnum
CREATE TYPE "PoolStatus" AS ENUM ('active', 'inactive', 'maintenance');

-- CreateEnum
CREATE TYPE "AgentTypeStatus" AS ENUM ('active', 'inactive', 'deprecated');

-- CreateEnum
CREATE TYPE "AgentInstanceStatus" AS ENUM ('login', 'ready', 'busy', 'paused', 'logout');

-- CreateEnum
CREATE TYPE "SkillStatus" AS ENUM ('active', 'inactive', 'deprecated');

-- CreateTable
CREATE TABLE "pools" (
    "id" TEXT NOT NULL,
    "pool_id" TEXT NOT NULL,
    "tenant_id" TEXT NOT NULL,
    "status" "PoolStatus" NOT NULL DEFAULT 'active',
    "description" TEXT,
    "channel_types" TEXT[],
    "sla_target_ms" INTEGER NOT NULL,
    "routing_expression" JSONB,
    "evaluation_template_id" TEXT,
    "supervisor_config" JSONB,
    "created_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updated_at" TIMESTAMP(3) NOT NULL,
    "created_by" TEXT NOT NULL,

    CONSTRAINT "pools_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "agent_types" (
    "id" TEXT NOT NULL,
    "agent_type_id" TEXT NOT NULL,
    "tenant_id" TEXT NOT NULL,
    "status" "AgentTypeStatus" NOT NULL DEFAULT 'active',
    "framework" TEXT NOT NULL,
    "execution_model" TEXT NOT NULL,
    "role" TEXT NOT NULL DEFAULT 'executor',
    "max_concurrent_sessions" INTEGER NOT NULL DEFAULT 1,
    "skills" JSONB NOT NULL DEFAULT '[]',
    "permissions" TEXT[] DEFAULT ARRAY[]::TEXT[],
    "capabilities" JSONB NOT NULL DEFAULT '{}',
    "prompt_id" TEXT,
    "agent_classification" JSONB,
    "traffic_weight" DOUBLE PRECISION NOT NULL DEFAULT 1.0,
    "created_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updated_at" TIMESTAMP(3) NOT NULL,
    "created_by" TEXT NOT NULL,

    CONSTRAINT "agent_types_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "agent_type_pools" (
    "agent_type_id" TEXT NOT NULL,
    "pool_id" TEXT NOT NULL,
    "registered_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "agent_type_pools_pkey" PRIMARY KEY ("agent_type_id","pool_id")
);

-- CreateTable
CREATE TABLE "agent_instances" (
    "id" TEXT NOT NULL,
    "instance_id" TEXT NOT NULL,
    "tenant_id" TEXT NOT NULL,
    "agent_type_id" TEXT NOT NULL,
    "session_token" TEXT,
    "current_sessions" INTEGER NOT NULL DEFAULT 0,
    "status" "AgentInstanceStatus" NOT NULL DEFAULT 'login',
    "created_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updated_at" TIMESTAMP(3) NOT NULL,

    CONSTRAINT "agent_instances_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "skills" (
    "id" TEXT NOT NULL,
    "skill_id" TEXT NOT NULL,
    "tenant_id" TEXT NOT NULL,
    "status" "SkillStatus" NOT NULL DEFAULT 'active',
    "name" TEXT NOT NULL,
    "version" TEXT NOT NULL,
    "description" TEXT NOT NULL,
    "classification" JSONB NOT NULL,
    "instruction" JSONB NOT NULL,
    "tools" JSONB NOT NULL DEFAULT '[]',
    "interface_schema" JSONB,
    "evaluation" JSONB,
    "knowledge_domains" TEXT[] DEFAULT ARRAY[]::TEXT[],
    "compatibility" JSONB,
    "flow" JSONB,
    "created_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updated_at" TIMESTAMP(3) NOT NULL,
    "created_by" TEXT NOT NULL,

    CONSTRAINT "skills_pkey" PRIMARY KEY ("id")
);

-- CreateIndex
CREATE INDEX "pools_tenant_id_idx" ON "pools"("tenant_id");

-- CreateIndex
CREATE UNIQUE INDEX "pools_pool_id_tenant_id_key" ON "pools"("pool_id", "tenant_id");

-- CreateIndex
CREATE INDEX "agent_types_tenant_id_idx" ON "agent_types"("tenant_id");

-- CreateIndex
CREATE INDEX "agent_types_tenant_id_status_idx" ON "agent_types"("tenant_id", "status");

-- CreateIndex
CREATE UNIQUE INDEX "agent_types_agent_type_id_tenant_id_key" ON "agent_types"("agent_type_id", "tenant_id");

-- CreateIndex
CREATE UNIQUE INDEX "agent_instances_session_token_key" ON "agent_instances"("session_token");

-- CreateIndex
CREATE INDEX "agent_instances_tenant_id_idx" ON "agent_instances"("tenant_id");

-- CreateIndex
CREATE INDEX "agent_instances_tenant_id_status_idx" ON "agent_instances"("tenant_id", "status");

-- CreateIndex
CREATE UNIQUE INDEX "agent_instances_instance_id_tenant_id_key" ON "agent_instances"("instance_id", "tenant_id");

-- CreateIndex
CREATE INDEX "skills_tenant_id_idx" ON "skills"("tenant_id");

-- CreateIndex
CREATE INDEX "skills_tenant_id_status_idx" ON "skills"("tenant_id", "status");

-- CreateIndex
CREATE UNIQUE INDEX "skills_skill_id_tenant_id_key" ON "skills"("skill_id", "tenant_id");

-- AddForeignKey
ALTER TABLE "agent_type_pools" ADD CONSTRAINT "agent_type_pools_agent_type_id_fkey" FOREIGN KEY ("agent_type_id") REFERENCES "agent_types"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "agent_type_pools" ADD CONSTRAINT "agent_type_pools_pool_id_fkey" FOREIGN KEY ("pool_id") REFERENCES "pools"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "agent_instances" ADD CONSTRAINT "agent_instances_agent_type_id_tenant_id_fkey" FOREIGN KEY ("agent_type_id", "tenant_id") REFERENCES "agent_types"("agent_type_id", "tenant_id") ON DELETE RESTRICT ON UPDATE CASCADE;
