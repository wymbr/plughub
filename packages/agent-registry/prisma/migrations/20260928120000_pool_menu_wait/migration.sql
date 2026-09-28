-- `pools.menu_wait` — como o agente de IA do pool espera o cliente num `menu` (DUR-01).
--
--   block (padrão): o executor espera dentro da requisição (BLPOP), segurando conexão
--                   Redis, lock e requisição HTTP a conversa inteira — como sempre foi.
--   park          : a conversa ESTACIONA; a resposta do cliente a acorda.
--
-- POR QUE UMA COLUNA NO POOL
-- ==========================
-- É config de NEGÓCIO com rollout pool a pool (ADR adr-menu-durable-park.md, D9): ligar
-- num pool piloto, medir no harness de carga e voltar sem deploy. O store dela é o
-- agent-registry, editável na tela — nunca env.
--
-- NOT NULL com DEFAULT: todo pool existente fica em `block`, então nada muda no ar até
-- alguém ligar `park` num pool.

ALTER TABLE "pools" ADD COLUMN IF NOT EXISTS "menu_wait" TEXT NOT NULL DEFAULT 'block';
