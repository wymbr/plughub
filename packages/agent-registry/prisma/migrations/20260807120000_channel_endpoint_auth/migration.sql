-- Arco webhook-endpoint-auth (aberto pela Fase F do ADR adr-webhook-endpoint-single-registry):
-- autenticação OPCIONAL por endpoint de canal.
--
-- O QUE ISTO CONSERTA. Até 2026-08-07, `ChannelEndpoint` não tinha autenticação
-- alguma — nas DUAS portas. Todo disparo de webhook era anônimo, contra pools que
-- promovem deploy e contatam clientes. O único caminho autenticado era o registro
-- LEGADO por token (`workflow.webhooks`), cuja auth estava acoplada a um ciclo de
-- vida de instância morto (mutadores 410). A Fase F separou as duas coisas: o
-- legado é aposentado, e a autenticação vira requisito de plataforma, aqui.
--
-- POR QUE `auth_required` NASCE FALSE. Ligar por padrão converteria todo endpoint em
-- uso num 401 retroativo — inclusive os dez internos e o `crm-callback`. O risco
-- conhecido do opt-in é virar segurança que ninguém liga; o antídoto escolhido NÃO é
-- o default agressivo, e sim tornar a ausência **medida**: o probe de inventário
-- conta os endpoints anônimos a cada execução e a tela os marca. É o mesmo movimento
-- da Fase A do ADR — ausência honesta vira presença declarada.
--
-- O SEGREDO NÃO É PERSISTIDO. Guarda-se o SHA-256 (`token_hash`) e os 16 primeiros
-- caracteres do token em claro (`token_prefix`). O prefixo existe para identificar
-- QUAL token está numa linha (tela, log, rotação) sem dar material de força bruta —
-- 16 chars de um segredo de 256 bits não estreitam a busca de forma útil. O token em
-- claro é devolvido UMA vez, na criação/rotação, e nunca mais.
--
-- ⚠️ `token_hash` é material de credencial e NÃO pode sair no `GET` geral: é o mesmo
-- endpoint que a UI consome. Ele só é devolvido a chamador que apresente
-- `x-service-token` (o channel-gateway). Isso é enforcement de aplicação, não de
-- schema — está em `routes/channel-endpoints.ts`.

ALTER TABLE "channel_endpoints"
  ADD COLUMN IF NOT EXISTS "auth_required" BOOLEAN NOT NULL DEFAULT false,
  ADD COLUMN IF NOT EXISTS "token_hash"    TEXT,
  ADD COLUMN IF NOT EXISTS "token_prefix"  TEXT;

-- Busca por hash na verificação (o gateway resolve por identificador, mas uma
-- rotação/auditoria futura procura pelo hash). Parcial: só linhas que têm token.
CREATE INDEX IF NOT EXISTS "channel_endpoints_token_hash_idx"
  ON "channel_endpoints" ("token_hash")
  WHERE "token_hash" IS NOT NULL;
