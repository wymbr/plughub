-- Drop `pools.acw_gate` (adicionada em 20260724000000_pool_acw_gate).
--
-- A Camada C do detach de hooks entregou o mecanismo (`acw_gate: none|soft|hard`
-- + marker `{t}:instance:{iid}:acw_pending` + regra em `get_ready_instances`), e a
-- Phase 0 do wrap-up unificado o REVERTEU por ser o modelo errado: bloqueava a
-- instância INTEIRA em vez de uma vaga, e reservava no dispatch em vez de no claim.
-- A capacidade de wrap-up passou a ser 1 vaga pelo semáforo `claim_instance`, igual
-- nos dois modos (inline e detached).
--
-- A reversão levou o enforcement e o smoke, mas deixou a COLUNA e todo o plumbing
-- (UI → agent-registry → Kafka → routing-engine) de pé, sem nenhum leitor: um
-- operador que marcasse "hard" na tela de Pools acreditava ter armado um gate
-- inexistente. Config que não decide nada é pior que config ausente.
--
-- Se um gate de ACW voltar, será desenhado sobre a VAGA e terá semântica própria —
-- esta coluna não deve ser ressuscitada.

ALTER TABLE "pools"
  DROP COLUMN IF EXISTS "acw_gate";
