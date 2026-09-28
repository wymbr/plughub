-- DUR-01 F4 — `pools.menu_wait` sai.
--
-- A coluna existiu para o rollout pool a pool do menu estacionado (ADR
-- adr-menu-durable-park.md, D9). Desde a F4 estacionar é o modo de toda espera de `menu`
-- que o bridge sabe acordar, e o bloqueio sobra só dentro de `begin_transaction` (D2) e
-- para chamadores do `/execute` que não sabem acordar — decisão do CÓDIGO, não do pool.
-- Um campo de config que não muda nada é o "valor plausível": a tela diria `block` e a
-- conversa estacionaria. Sai inteiro, com a tela e o schema.

ALTER TABLE "pools" DROP COLUMN IF EXISTS "menu_wait";
