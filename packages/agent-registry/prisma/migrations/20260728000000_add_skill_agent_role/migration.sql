-- Resíduo `role` (2026-07-28) — Fatia A: propósito do agente como fato do REGISTRY.
--
-- Contexto: quatro sites liam `role` de `{tenant}:agent:instance:{participant_id}`
-- e nenhum produtor escrevia o campo. A investigação mostrou que o nome `role`
-- cobria DOIS fatos de escopos diferentes:
--
--   (A) propósito do agente  — executor | orchestrator | evaluator
--       escopo: o ARTEFATO (estável, declarado, config do tenant)
--   (B) papel de participação — primary | specialist | supervisor
--       escopo: (participante, sessão) — a mesma instância é primary numa
--       sessão e specialist noutra AO MESMO TEMPO
--
-- Esta coluna cobre (A). Vive no skill — e não num agent_type — porque a
-- entidade AgentType foi aposentada: no modelo deploy-driven a identidade do
-- agente É o skill deployado (`agent_login` valida contra GET /v1/skills/{id}).
--
-- Default 'executor' mantém todo skill existente inalterado E fecha por omissão:
-- um skill que não declara nada NÃO ganha privilégio de avaliador.

ALTER TABLE "skills"
  ADD COLUMN "agent_role" TEXT NOT NULL DEFAULT 'executor';

-- Backfill dos skills de avaliação já provisionados.
--
-- Necessário porque o RegistrySyncer é SEED-IF-ABSENT: num ambiente onde a linha
-- já existe COM `flow`, o PUT do YAML é pulado e a declaração `agent_role: evaluator`
-- nunca chega ao DB. Sem este UPDATE o avaliador ficaria em 'executor' e o gate
-- (agora fechado) o negaria — a correção de segurança derrubaria o pipeline.
--
-- A alternativa seria rodar com REGISTRY_SYNC_RECONCILE=true, que tem efeito
-- colateral pior: o YAML sobrescreve o flow editado na UI.
--
-- Escopo deliberadamente estreito: só os skills cujo PROPÓSITO é avaliar. Não é
-- uma lista de config — é o backfill pontual da introdução da coluna.
UPDATE "skills"
   SET "agent_role" = 'evaluator'
 WHERE "skill_id" IN ('skill_avaliacao_v1', 'skill_revisao_v1', 'skill_pre_revisao_v1');
