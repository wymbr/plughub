-- CreateTable
CREATE TABLE "journey_types" (
    "id" TEXT NOT NULL,
    "journey_type_id" TEXT NOT NULL,
    "tenant_id" TEXT NOT NULL,
    "sla_ms" INTEGER,
    "description" TEXT,
    "created_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updated_at" TIMESTAMP(3) NOT NULL,

    CONSTRAINT "journey_types_pkey" PRIMARY KEY ("id")
);

-- CreateUniqueIndex
CREATE UNIQUE INDEX "journey_types_journey_type_id_tenant_id_key" ON "journey_types"("journey_type_id", "tenant_id");

-- CreateIndex
CREATE INDEX "journey_types_tenant_id_idx" ON "journey_types"("tenant_id");
