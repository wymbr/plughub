-- Sincroniza o histórico de migrations com o schema.prisma.
--
-- Cinco mudanças estruturais entraram em schema.prisma e chegaram às bases VIVAS
-- pelo `db push` (o caminho de boot da época), mas NUNCA viraram migration. Enquanto
-- toda base era db-push, isso era invisível: o push aplica o schema.prisma direto, e
-- a base sempre batia. Numa base NOVA, porém, `migrate deploy` constrói a forma
-- ANTIGA — e o sanity check do bootstrap-db recusa subir, corretamente:
--
--   [-] Removed enums:  AgentTypeStatus
--   [-] Removed tables: agent_types, agent_type_pools, skill_version_slots
--   [+] pools.agent_kind
--   [+] skills.flow_draft
--
-- Contexto das remoções (não são perda de dado em uso):
--   • agent_types / agent_type_pools — o eixo agent_type foi aposentado; a relação
--     virou coluna simples em agent_instances (Fase 3d/C). `Pool.agent_kind` é a
--     autoridade canônica de tipagem humano/IA.
--   • skill_version_slots — superseded por pool_skill_slots (deploy é pool-centric).
--
-- Escrita DEFENSIVAMENTE (IF EXISTS / IF NOT EXISTS): bases db-push legadas já têm
-- agent_kind e flow_draft, e podem ou não ainda ter as tabelas mortas. Um
-- `ADD COLUMN` cru quebraria justamente as instalações que hoje funcionam.

-- ── FKs para as tabelas removidas ────────────────────────────────────────────
ALTER TABLE "agent_instances"  DROP CONSTRAINT IF EXISTS "agent_instances_agent_type_id_tenant_id_fkey";
ALTER TABLE "agent_type_pools" DROP CONSTRAINT IF EXISTS "agent_type_pools_agent_type_id_fkey";
ALTER TABLE "agent_type_pools" DROP CONSTRAINT IF EXISTS "agent_type_pools_pool_id_fkey";
ALTER TABLE "skill_version_slots" DROP CONSTRAINT IF EXISTS "skill_version_slots_skill_id_tenant_id_fkey";

-- ── Tabelas e enum aposentados ───────────────────────────────────────────────
DROP TABLE IF EXISTS "agent_type_pools";
DROP TABLE IF EXISTS "skill_version_slots";
DROP TABLE IF EXISTS "agent_types";
DROP TYPE  IF EXISTS "AgentTypeStatus";

-- ── Colunas que só existiam via db push ──────────────────────────────────────
-- Pool.agent_kind: "human" | "ai". Nullable de propósito — backfill por inferência
-- no boot (slot de deploy ⇒ ai; senão human).
ALTER TABLE "pools"  ADD COLUMN IF NOT EXISTS "agent_kind" TEXT;

-- Skill.flow_draft: rascunho do editor. O PUT escreve aqui; só o deploy copia
-- draft→flow. Nunca vaza para produção.
ALTER TABLE "skills" ADD COLUMN IF NOT EXISTS "flow_draft" JSONB;
