-- ADR adr-webhook-endpoint-single-registry (Fase B) — `channel_endpoints.origin`:
-- procedência da linha. `external` (cadastrada pelo tenant) · `internal` (declarada
-- no provisionamento, read-only na tela) · `legacy_token` (migrada de workflow.webhooks).
--
-- POR QUE UMA COLUNA, e não convenção de nome nem chave em `settings`. A D2 decide
-- que o `identifier` é OPACO: não codifica qual skill roda e **nada o interpreta**.
-- Sem esta coluna, a única forma de a tela saber que uma linha é interna seria olhar
-- o texto do identificador (`skill_…`) e inferir — que é precisamente a semântica que
-- a D2 retira. Ou seja: a coluna não é conveniência de UI, é o que torna a opacidade
-- sustentável. Em `settings` (Json) o campo existiria sem tipo nem default, e a
-- ausência seria indistinguível de `external` por acidente em vez de por decisão.
--
-- DEFAULT `external` É A LEITURA CERTA DO PASSADO. Toda linha que já existe nasceu de
-- cadastro (a única superfície de escrita até hoje). Marcar o legado como `internal`
-- seria inventar procedência para dado que não a tem.
--
-- O QUE ESTA COLUNA NÃO FAZ. Não participa da resolução — endereço continua sendo
-- resolvido por (tenant, channel, identifier), e a D7 exige que a Fase B **não mude
-- comportamento**: semear antes de trocar a resolução, remover o fallback por último.
-- `origin` é inventário e governança de edição, não roteamento.

ALTER TABLE "channel_endpoints"
  ADD COLUMN IF NOT EXISTS "origin" TEXT NOT NULL DEFAULT 'external';
