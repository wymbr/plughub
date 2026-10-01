-- `pools.a2a` — contrato do pool exposto pelo canal `a2a` (AAS-01; adr-a2a-server-binding D3).
--
-- { "display_name": "...", "description": "...", "input_schema": {...}, "output_schema": {...},
--   "skills": [{"id": "...", "name": "...", "description": "...", "tags": [], "examples": []}],
--   "discoverable": false, "principal_kinds": ["partner"] }
--
-- POR QUE UMA COLUNA NO POOL
-- ==========================
-- O AgentCard é PROJEÇÃO do pool (D2): não existe card escrito à mão, e o contrato vive onde o
-- pool vive — editável na tela, versionado junto com o deploy pelo `set_at` do slot `current`.
--
-- Aditiva e anulável, mas NÃO opcional para quem usa: a rota recusa pool de contato com `a2a`
-- em `channel_types` sem esta coluna, e recusa a coluna sem o canal. Medido antes: nenhum pool
-- tem `a2a` (o valor nem existia no enum), então nenhum pool existente fica inválido.

ALTER TABLE "pools" ADD COLUMN IF NOT EXISTS "a2a" JSONB;
